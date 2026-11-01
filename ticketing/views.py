import re
from decimal import Decimal
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import hashlib
import io
import os
from PIL import Image, ImageDraw, ImageFont
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Ticket


def _serialize_ticket(t):
    """Formats an owned Ticket instance into a JSON-serializable dictionary."""
    if (not t.qr_token_hash or not t.qr_token) and t.status in ('ACTIVE', 'LISTED'):
        t.generate_qr_credential()
        t.save(update_fields=['qr_token', 'qr_token_hash'])

    ev = t.event
    pr = ev.provider
    resale = getattr(t, 'resale_listing', None)
    is_resale_active = resale and resale.status == 'ACTIVE'

    # Route & transit parsing for boarding pass aesthetics
    origin_name = ''
    dest_name = ''
    origin_code = ''
    dest_code = ''
    duration = ''
    vehicle = ''

    route_text = ev.venue_or_route if '→' in ev.venue_or_route else (ev.title if '→' in ev.title else '')
    if route_text:
        parts = route_text.split('→', 1)
        origin_name, dest_name = parts[0].strip(), parts[1].strip()

    def _extract_station_code(text, name):
        match = re.findall(r'\(([A-Z0-9]{3,4})\)', text) or re.findall(r'\(([A-Z0-9]{3,4})\)', name)
        if not match:
            match = re.findall(r'\b([A-Z0-9]{3,4})\b', f"{text} {name}")
        return match[0] if match else name[:4].upper()

    title_parts = ev.title.split('→', 1) if '→' in ev.title else [ev.title, '']
    if origin_name:
        origin_code = _extract_station_code(title_parts[0], origin_name)
    if dest_name:
        dest_code = _extract_station_code(title_parts[1], dest_name)

    if ev.end_time and ev.start_time:
        diff = ev.end_time - ev.start_time
        hours = diff.days * 24 + diff.seconds // 3600
        mins = (diff.seconds % 3600) // 60
        duration = f"{hours}H {mins}M" if mins else f"{hours}H"
        if ev.category == 'flight':
            duration += " (NON-STOP)"
    elif ev.itinerary_or_details:
        dur_m = re.search(r'(\d+h(?:\s*\d+m)?|\d+\s*H(?:\s*\d+\s*M)?|\d+\s*Hours?)', ev.itinerary_or_details, re.IGNORECASE)
        if dur_m:
            duration = dur_m.group(1).upper()

    if ev.itinerary_or_details:
        first_segment = ev.itinerary_or_details.split('•')[0].strip()
        if any(w in first_segment.lower() for w in ['airbus', 'boeing', 'express', 'sleeper', 'semi-sleeper', 'vande', 'rajdhani']):
            vehicle = first_segment
    if not vehicle:
        vehicle = ev.title.split(':')[0].strip() if ':' in ev.title else ev.title

    return {
        'id': t.id,
        'ticket_ref': t.ticket_ref,
        'booking_reference': t.booking_reference,
        'title': ev.title,
        'category': ev.category,
        'provider_name': pr.name,
        'provider_logo': pr.logo_url,
        'venue_or_route': ev.venue_or_route,
        'seat_or_coach': t.seat_or_coach,
        'start_time': ev.start_time.isoformat(),
        'end_time': ev.end_time.isoformat() if ev.end_time else None,
        'event_date': ev.start_time.isoformat(),
        'formatted_date': ev.start_time.strftime("%b %d, %Y, %I:%M %p"),
        'formatted_date_short': ev.start_time.strftime("%d %b %Y"),
        'departure_time': ev.start_time.strftime("%I:%M %p"),
        'arrival_time': ev.end_time.strftime("%I:%M %p") if ev.end_time else '',
        'has_route': bool(origin_code and dest_code),
        'origin_code': origin_code,
        'origin_name': origin_name,
        'dest_code': dest_code,
        'dest_name': dest_name,
        'duration': duration,
        'vehicle': vehicle,
        'itinerary_or_details': ev.itinerary_or_details,
        'image_url': ev.image_url,
        'face_value': str(t.face_value),
        'attendee_name': t.attendee_name,
        'status': t.status,
        'qr_token': t.qr_token,
        'qr_token_hash': t.qr_token_hash,
        'qr_image_url': t.get_qr_image_base64(),
        'resale_listing_id': resale.id if is_resale_active else None,
        'resale_asking_price': str(resale.asking_price) if is_resale_active else None,
        'is_eligible_for_resale': (t.status == 'ACTIVE' and ev.start_time > timezone.now()),
        'used_at': t.used_at.isoformat() if t.used_at else None,
        'created_at': t.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_user_tickets(request):
    """Returns all tickets owned by the authenticated user in their Ticket Vault."""
    tickets = Ticket.objects.filter(
        owner=request.user
    ).select_related('event', 'event__provider', 'resale_listing')

    results = [_serialize_ticket(t) for t in tickets]
    return Response({
        'count': len(results),
        'tickets': results
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ticket_detail(request, pk):
    """Returns details for a single ticket owned by the authenticated user."""
    try:
        t = Ticket.objects.select_related('event', 'event__provider', 'resale_listing').get(pk=pk, owner=request.user)
    except Ticket.DoesNotExist:
        return Response({'error': 'Ticket not found in your vault.'}, status=status.HTTP_404_NOT_FOUND)

    return Response({'ticket': _serialize_ticket(t)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_eligible_resale_tickets(request):
    """
    Returns owned tickets eligible to be listed for peer-to-peer resale.
    Requires user to own the ticket, be identity verified, status to be ACTIVE, and event date in the future.
    """
    if not request.user.is_identity_verified or not request.user.verified_legal_name:
        return Response({
            'error': 'Identity verification required.',
            'code': 'KYC_REQUIRED',
            'tickets': []
        }, status=status.HTTP_403_FORBIDDEN)

    tickets = Ticket.objects.filter(
        owner=request.user,
        status='ACTIVE',
        event__start_time__gt=timezone.now()
    ).select_related('event', 'event__provider').order_by('event__start_time')

    results = [
        {
            'id': t.id,
            'ticket_ref': t.ticket_ref,
            'booking_reference': t.booking_reference,
            'title': t.event.title,
            'category': t.event.category,
            'provider_name': t.event.provider.name,
            'venue_or_route': t.event.venue_or_route,
            'seat_or_coach': t.seat_or_coach,
            'event_date': t.event.start_time.isoformat(),
            'face_value': str(t.face_value),
            'max_allowed_resale_price': str((t.face_value * Decimal('1.20')).quantize(Decimal('0.01'))),
            'attendee_name': t.attendee_name,
        }
        for t in tickets
    ]

    return Response({
        'count': len(results),
        'tickets': results
    })


# ==============================================================================
#  Django Template Web Views & Pass Issuance
# ==============================================================================

def _get_font(size):
    """Safely attempts to load true-type system fonts with fallback to PIL default."""
    for path in [
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/SFNSMono.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/TTF/DejaVuSans.ttf',
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _generate_ticket_pdf_buffer(ticket):
    """
    Renders an authenticated digital ticket pass as a vector/raster PDF document
    containing event details, provider attribution, attendee name, and current QR credential.
    Uses Pillow and qrcode without requiring external document-generation services.
    """
    canvas = Image.new('RGB', (800, 1150), color='#ffffff')
    draw = ImageDraw.Draw(canvas)

    font_brand = _get_font(28)
    font_sub = _get_font(13)
    font_title = _get_font(24)
    font_section = _get_font(12)
    font_val_bold = _get_font(16)
    font_val = _get_font(14)

    ev = ticket.event
    pr = ev.provider

    # Top Brand Header Banner
    draw.rectangle([(0, 0), (800, 120)], fill='#0f172a')
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo-mark.png')
    text_x = 40
    if os.path.exists(logo_path):
        try:
            brand_logo = Image.open(logo_path).convert('RGBA')
            brand_logo.thumbnail((75, 75), Image.Resampling.LANCZOS)
            canvas.paste(brand_logo, (40, 22), brand_logo)
            text_x = 130
        except Exception:
            text_x = 40
    draw.text((text_x, 32), 'lastchance', fill='#ffffff', font=font_brand)
    draw.text((text_x, 72), 'VERIFIED DIGITAL ADMISSION PASS', fill='#ff6600', font=font_sub)
    draw.text((540, 48), f'CATEGORY: {ev.category.upper()}', fill='#ffffff', font=font_section)

    # Frame
    draw.rectangle([(20, 20), (780, 1130)], outline='#cbd5e1', width=2)

    # Provider & Event Header
    draw.text((40, 150), f'ISSUING PROVIDER: {pr.name.upper()}', fill='#64748b', font=font_section)
    draw.text((40, 175), ev.title, fill='#0f172a', font=font_title)
    draw.line([(40, 230), (760, 230)], fill='#e2e8f0', width=2)

    # Left Column: Passenger & Event Details
    formatted_date = ev.start_time.strftime("%b %d, %Y, %I:%M %p")
    details = [
        ('PASSENGER / ATTENDEE', ticket.attendee_name or (ticket.owner.verified_legal_name if ticket.owner else '') or (ticket.owner.username if ticket.owner else 'Ticket Holder')),
        ('VENUE / ROUTE', ev.venue_or_route),
        ('DATE & SCHEDULED TIME', formatted_date),
        ('SEAT / COACH ALLOCATION', ticket.seat_or_coach),
        ('BOOKING REFERENCE', ticket.booking_reference or 'N/A'),
        ('AUTHORITATIVE TICKET REF', ticket.ticket_ref),
    ]

    y = 260
    for label, val in details:
        draw.text((40, y), label, fill='#64748b', font=font_section)
        draw.text((40, y + 20), str(val), fill='#0f172a', font=font_val_bold if 'REF' in label or 'PASSENGER' in label else font_val)
        y += 65

    # Right Column: QR Code Box
    draw.rectangle([(480, 260), (740, 520)], outline='#0f172a', width=2, fill='#f8fafc')

    # Generate QR in memory from current qr credential without reading/writing disk
    if not ticket.qr_token or not ticket.qr_token_hash:
        ticket.generate_qr_credential()
        ticket.save(update_fields=['qr_token', 'qr_token_hash'])

    qr_img = ticket.get_qr_image_pil()
    if qr_img:
        qr_resized = qr_img.resize((200, 200))
        canvas.paste(qr_resized, (510, 280))

    draw.text((505, 490), 'DIGITAL TICKET CREDENTIAL', fill='#0f172a', font=font_section)

    # Notice Banner
    draw.rectangle([(40, 720), (760, 840)], fill='#f8fafc', outline='#cbd5e1', width=1)
    draw.text((60, 740), 'LASTCHANCE PLATFORM NOTICE', fill='#0f172a', font=font_section)
    policy_text = (
        '• This is an authoritative digital ticket issued by lastchance authorized providers.\n'
        '• Associated with unique QR credential token for peer-to-peer resale.\n'
        '• Upon ownership transfer, the previous credential is automatically invalidated.'
    )
    draw.text((60, 765), policy_text, fill='#475569', font=font_sub)

    # Footer
    draw.line([(40, 1060), (760, 1060)], fill='#e2e8f0', width=1)
    draw.text((40, 1080), f'lastchance Peer-to-Peer Resale & Ticketing Gateway • Status: {ticket.status}', fill='#94a3b8', font=font_sub)

    buf = io.BytesIO()
    canvas.save(buf, format='PDF', resolution=150.0)
    return buf.getvalue()


@login_required
@require_http_methods(['GET'])
def ticket_vault_view(request):
    """
    Ticket Vault view showing the authenticated user's owned tickets.
    Preserves authoritative status and generates QR credentials where applicable.
    """
    tickets = Ticket.objects.filter(
        owner=request.user
    ).select_related('event', 'event__provider', 'resale_listing')

    serialized_tickets = [_serialize_ticket(t) for t in tickets]

    counts = {
        'active_count': 0,
        'listed_count': 0,
        'used_count': 0,
        'train_count': 0,
        'flight_count': 0,
        'concert_count': 0,
        'movie_count': 0,
        'sports_count': 0,
    }

    for t in serialized_tickets:
        st = t['status']
        if st == 'ACTIVE':
            counts['active_count'] += 1
        elif st == 'LISTED':
            counts['listed_count'] += 1
        elif st in ('USED', 'CANCELLED'):
            counts['used_count'] += 1

        cat = t['category'].lower()
        if 'train' in cat:
            counts['train_count'] += 1
        elif 'flight' in cat:
            counts['flight_count'] += 1
        elif any(k in cat for k in ('concert', 'festival', 'live')):
            counts['concert_count'] += 1
        elif any(k in cat for k in ('movie', 'cinema')):
            counts['movie_count'] += 1
        elif 'sports' in cat:
            counts['sports_count'] += 1

    context = {
        'tickets': serialized_tickets,
        'count': len(serialized_tickets),
        **counts,
    }
    return render(request, 'ticketing/vault.html', context)


@login_required
@require_http_methods(['GET'])
def ticket_detail_view(request, pk):
    """
    Ticket detail view displaying full ticket information and digital pass.
    Strictly verifies ownership: users can never access another user's ticket.
    """
    t = get_object_or_404(
        Ticket.objects.select_related('event', 'event__provider', 'resale_listing'),
        pk=pk,
        owner=request.user
    )
    return render(request, 'ticketing/ticket_detail.html', {'ticket': _serialize_ticket(t)})


@login_required
@require_http_methods(['GET'])
def download_ticket_pdf_view(request, pk):
    """
    Generates and downloads an authenticated PDF ticket pass.
    Strictly verifies that the requester is the current authoritative owner of the ticket.
    If the ticket was resold, the previous holder (seller) receives HTTP 404.
    """
    t = get_object_or_404(
        Ticket.objects.select_related('event', 'event__provider'),
        pk=pk,
        owner=request.user
    )

    if not t.qr_token or not t.qr_token_hash:
        t.generate_qr_credential(request=request)
        t.save(update_fields=['qr_token', 'qr_token_hash'])

    pdf_bytes = _generate_ticket_pdf_buffer(t)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="ticket_{t.ticket_ref}.pdf"'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@require_http_methods(['GET'])
def ticket_qr_validate_view(request, credential):
    """
    Validates QR credential link scanned from a digital or PDF ticket.
    1. Computes SHA-256 hash of credential from URL.
    2. Compares hash against current authoritative Ticket.qr_token_hash.
    3. If invalid, outdated, or revoked, renders invalid ticket page without exposing private info.
    4. If valid, applies existing authentication and ownership rules before displaying details.
    """
    if not credential:
        return render(request, 'ticketing/ticket_invalid.html', {
            'error_title': 'Invalid Ticket Pass',
            'error_message': 'No ticket credential was provided.'
        }, status=404)

    cred_hash = hashlib.sha256(credential.encode('utf-8')).hexdigest()

    try:
        ticket = Ticket.objects.select_related(
            'event', 'event__provider', 'resale_listing'
        ).get(qr_token_hash=cred_hash)
    except Ticket.DoesNotExist:
        return render(request, 'ticketing/ticket_invalid.html', {
            'error_title': 'Invalid Ticket Credential',
            'error_message': 'This ticket QR credential is invalid, outdated, or has been revoked due to a resale transfer.'
        }, status=404)

    if ticket.status == 'CANCELLED':
        return render(request, 'ticketing/ticket_invalid.html', {
            'error_title': 'Ticket Cancelled',
            'error_message': 'This ticket is no longer active.'
        }, status=404)

    # Valid credential: apply authentication and ownership rules
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")

    if ticket.owner != request.user:
        raise Http404('Ticket not found in your vault.')

    return render(request, 'ticketing/ticket_detail.html', {'ticket': _serialize_ticket(ticket)})
