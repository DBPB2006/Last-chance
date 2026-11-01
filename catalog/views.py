import re
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ticketing.models import Provider, Event, Ticket, ResaleListing
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods, require_POST


CATEGORIES = [
    {'id': 1, 'name': 'Flights', 'slug': 'flight', 'icon': 'Plane'},
    {'id': 2, 'name': 'Trains', 'slug': 'train', 'icon': 'Train'},
    {'id': 3, 'name': 'Movies', 'slug': 'movie', 'icon': 'Film'},
    {'id': 4, 'name': 'Concerts', 'slug': 'concert', 'icon': 'Music'},
    {'id': 5, 'name': 'Festivals', 'slug': 'festival', 'icon': 'Sparkles'},
    {'id': 6, 'name': 'Sports', 'slug': 'sports', 'icon': 'Trophy'},
    {'id': 7, 'name': 'Other Events', 'slug': 'other', 'icon': 'Calendar'},
]


@api_view(['GET'])
@permission_classes([AllowAny])
def list_categories(request):
    """Returns all supported ticketing categories."""
    return Response(CATEGORIES)


@api_view(['GET'])
@permission_classes([AllowAny])
def list_providers(request):
    """Returns active providers, optionally filtered by category."""
    category = request.GET.get('category', '').strip().lower()
    qs = Provider.objects.filter(is_active=True)
    if category and category != 'all':
        qs = qs.filter(category=category)

    results = [
        {
            'id': p.id,
            'name': p.name,
            'slug': p.slug,
            'category': p.category,
            'category_display': p.get_category_display(),
            'description': p.description,
            'logo_url': p.logo_url,
            'website': p.website,
        }
        for p in qs
    ]
    return Response({'providers': results})


def _cleanup_expired_reservations():
    """Releases expired reservations back to available or active status."""
    now = timezone.now()
    # Release direct tickets
    Ticket.objects.filter(
        status='RESERVED',
        owner__isnull=True,
        reserved_until__lt=now
    ).update(status='AVAILABLE', reserved_by=None, reserved_until=None)

    # Release resale listings in bulk
    expired_resales = ResaleListing.objects.filter(
        status='RESERVED',
        reserved_until__lt=now
    )
    if expired_resales.exists():
        ticket_ids = list(expired_resales.values_list('ticket_id', flat=True))
        expired_resales.update(status='ACTIVE', reserved_by=None, reserved_until=None)
        Ticket.objects.filter(id__in=ticket_ids, status='RESERVED').update(
            status='LISTED', reserved_by=None, reserved_until=None
        )


def _format_event_meta(ticket):
    ev = ticket.event
    pr = ev.provider
    return {
        'ticket_id': ticket.id,
        'ticket_ref': ticket.ticket_ref,
        'title': ev.title,
        'category': ev.category,
        'provider_name': pr.name,
        'provider_logo': pr.logo_url,
        'venue_or_route': ev.venue_or_route,
        'start_time': ev.start_time.isoformat(),
        'end_time': ev.end_time.isoformat() if ev.end_time else None,
        'event_date': ev.start_time.isoformat(),
        'formatted_date': ev.start_time.strftime("%b %d, %Y, %I:%M %p"),
        'seat_or_coach': ticket.seat_or_coach,
        'itinerary_or_details': ev.itinerary_or_details,
        'image_url': ev.image_url,
        'face_value': str(ticket.face_value),
        'created_at': ticket.created_at.isoformat(),
    }


def _format_direct_listing(t, is_available=True):
    data = _format_event_meta(t)
    data.update({
        'id': f"dir-{t.id}",
        'item_type': 'DIRECT',
        'resale_listing_id': None,
        'price': str(t.face_value),
        'asking_price': str(t.face_value),
        'service_fee': '0.00',
        'total_price': str(t.face_value),
        'status': t.status if not is_available else 'ACTIVE',
        'is_available': is_available,
        'seller_username': None,
        'is_direct': True,
    })
    return data


def _format_resale_listing(r):
    data = _format_event_meta(r.ticket)
    data.update({
        'id': f"res-{r.id}",
        'item_type': 'RESALE',
        'resale_listing_id': r.id,
        'price': str(r.total_buyer_price),
        'asking_price': str(r.asking_price),
        'service_fee': str(r.service_fee),
        'total_price': str(r.total_buyer_price),
        'face_value': str(r.ticket.face_value),
        'status': r.status,
        'is_available': r.is_currently_available,
        'seller_username': r.seller.username,
        'is_direct': False,
        'created_at': r.created_at.isoformat(),
    })
    return data


def _parse_filter_params(request):
    """Extracts and parses common inventory filter parameters."""
    def _parse_decimal(val):
        if val:
            try:
                d = Decimal(str(val).strip())
                if d.is_finite():
                    return d
            except (InvalidOperation, TypeError):
                pass
        return None

    return {
        'item_type': request.GET.get('type', 'all').lower(),
        'category': request.GET.get('category', '').strip().lower(),
        'q': request.GET.get('q', '').strip(),
        'provider_slug': request.GET.get('provider', '').strip().lower(),
        'min_price': _parse_decimal(request.GET.get('min_price')),
        'max_price': _parse_decimal(request.GET.get('max_price')),
        'sort': request.GET.get('sort', 'date_asc'),
    }


def _get_marketplace_listings(request):
    """
    Authoritative query and filter engine for DRF marketplace inventory endpoint.
    """
    _cleanup_expired_reservations()

    params = _parse_filter_params(request)
    item_type = params['item_type']
    category = params['category']
    q = params['q']
    provider_slug = params['provider_slug']
    parsed_min_price = params['min_price']
    parsed_max_price = params['max_price']
    sort = params['sort']

    results = []
    now = timezone.now()

    # 1. DIRECT BOOKING INVENTORY (Pre-created provider tickets)
    if item_type in ('all', 'direct'):
        direct_tickets = Ticket.objects.filter(
            status='AVAILABLE',
            owner__isnull=True,
            event__is_active=True,
            event__start_time__gt=now
        ).select_related('event', 'event__provider')

        if category and category != 'all':
            direct_tickets = direct_tickets.filter(event__category=category)
        if provider_slug:
            direct_tickets = direct_tickets.filter(event__provider__slug=provider_slug)
        if q:
            direct_tickets = direct_tickets.filter(
                Q(event__title__icontains=q) |
                Q(event__venue_or_route__icontains=q) |
                Q(event__provider__name__icontains=q) |
                Q(seat_or_coach__icontains=q)
            )
        if parsed_min_price is not None:
            direct_tickets = direct_tickets.filter(face_value__gte=parsed_min_price)
        if parsed_max_price is not None:
            direct_tickets = direct_tickets.filter(face_value__lte=parsed_max_price)

        for t in direct_tickets:
            results.append(_format_direct_listing(t))

    # 2. PEER-TO-PEER RESALE LISTINGS
    if item_type in ('all', 'resale'):
        resales = ResaleListing.objects.filter(
            status='ACTIVE',
            ticket__event__is_active=True,
            ticket__event__start_time__gt=now
        ).select_related('ticket', 'ticket__event', 'ticket__event__provider', 'seller')

        if category and category != 'all':
            resales = resales.filter(ticket__event__category=category)
        if provider_slug:
            resales = resales.filter(ticket__event__provider__slug=provider_slug)
        if q:
            resales = resales.filter(
                Q(ticket__event__title__icontains=q) |
                Q(ticket__event__venue_or_route__icontains=q) |
                Q(ticket__event__provider__name__icontains=q) |
                Q(ticket__seat_or_coach__icontains=q)
            )
        if parsed_min_price is not None:
            resales = resales.filter(asking_price__gte=parsed_min_price)
        if parsed_max_price is not None:
            resales = resales.filter(asking_price__lte=parsed_max_price)

        for r in resales:
            results.append(_format_resale_listing(r))

    # Sorting
    if sort == 'price_asc':
        results.sort(key=lambda x: Decimal(x['total_price']))
    elif sort == 'price_desc':
        results.sort(key=lambda x: Decimal(x['total_price']), reverse=True)
    elif sort == 'date_desc':
        results.sort(key=lambda x: x['event_date'], reverse=True)
    else:
        results.sort(key=lambda x: x['event_date'])

    return results


@api_view(['GET'])
@permission_classes([AllowAny])
def list_marketplace_listings(request):
    """
    Public inventory and marketplace browsing.
    Supports filtering by category, type (direct/resale/all), provider, search keyword, and price.
    """
    results = _get_marketplace_listings(request)
    return Response({
        'count': len(results),
        'listings': results
    })


def _parse_transit_info(ev):
    """Parses factual departure/arrival airport codes, stations, times and duration from an event."""
    venue = (ev.venue_or_route or '').strip()
    title = (ev.title or '').strip()
    details = (ev.itinerary_or_details or '').strip()

    # Split route by arrow
    parts = re.split(r'\s*(?:→|->)\s*', venue)
    orig_name = parts[0].strip() if len(parts) > 0 else venue
    dest_name = parts[1].strip() if len(parts) > 1 else ''

    orig_code = ''
    dest_code = ''

    # Check title first (e.g. Flight 6E-204: Mumbai (BOM) → New Delhi (DEL))
    title_parts = re.split(r'\s*(?:→|->)\s*', title)
    if len(title_parts) >= 2:
        m1 = re.findall(r'\(([A-Z]{3,4})\)', title_parts[0])
        m2 = re.findall(r'\(([A-Z]{3,4})\)', title_parts[1])
        if m1:
            orig_code = m1[-1]
        if m2:
            dest_code = m2[-1]

    if not orig_code and len(parts) > 0:
        m1 = re.findall(r'\(([A-Z]{3,4})\)', parts[0])
        if m1:
            orig_code = m1[-1]
    if not dest_code and len(parts) > 1:
        m2 = re.findall(r'\(([A-Z]{3,4})\)', parts[1])
        if m2:
            dest_code = m2[-1]

    # Clean redundant codes from station/airport name if code matches
    orig_clean = re.sub(rf'\s*\({orig_code}\)', '', orig_name).strip() if orig_code else orig_name
    dest_clean = re.sub(rf'\s*\({dest_code}\)', '', dest_name).strip() if dest_code else dest_name

    # If still no code, take first 3-4 letters uppercase of city or word
    if not orig_code and orig_name:
        words = re.findall(r'[A-Za-z]+', orig_name)
        orig_code = words[0][:3].upper() if words else 'DEP'
    if not dest_code and dest_name:
        words = re.findall(r'[A-Za-z]+', dest_name)
        dest_code = words[0][:3].upper() if words else 'ARR'

    dep_time = ev.start_time.strftime('%I:%M %p') if ev.start_time else ''
    arr_time = ev.end_time.strftime('%I:%M %p') if ev.end_time else ''

    duration_str = ''
    if ev.start_time and ev.end_time:
        diff = ev.end_time - ev.start_time
        tot_min = int(diff.total_seconds() // 60)
        h = tot_min // 60
        m = tot_min % 60
        if h > 0 and m > 0:
            duration_str = f"{h}h {m}m"
        elif h > 0:
            duration_str = f"{h}h"
        elif m > 0:
            duration_str = f"{m}m"

    bullets = [b.strip() for b in details.split('•')] if details else []

    gate_or_platform = ''
    for b in bullets:
        if re.search(r'\b(Gate|Platform|PF|Terminal)\b', b, re.IGNORECASE):
            gate_or_platform = b
            break

    middle_badge = ''
    for b in bullets:
        if re.search(r'\b(baggage|cabin|economy|business|class|ac|sleeper|airbus|boeing)\b', b, re.IGNORECASE):
            middle_badge = b
            break

    train_num_match = re.search(r'\((\d{4,5})\)', title)
    train_number = f"Train {train_num_match.group(1)}" if train_num_match else ''

    return {
        'orig_code': orig_code,
        'orig_name': orig_clean or orig_name,
        'dest_code': dest_code,
        'dest_name': dest_clean or dest_name,
        'dep_time': dep_time,
        'arr_time': arr_time,
        'duration_str': duration_str,
        'gate_or_platform': gate_or_platform,
        'middle_badge': middle_badge,
        'train_number': train_number,
    }


def _parse_event_details(ev):
    """Extracts factual venue access and tier details from event details."""
    details = (ev.itinerary_or_details or '').strip()
    bullets = [b.strip() for b in details.split('•')] if details else []
    venue_access = bullets[0] if bullets else ''
    venue_tag = bullets[1] if len(bullets) > 1 else ''
    return {
        'venue_access': venue_access,
        'venue_tag': venue_tag,
    }


def _get_event_listings(request):
    """
    Authoritative query engine for event-level browsing on the Explore page.
    Returns one record per event with available direct-booking ticket counts and starting prices.
    Preserves category, search, provider, price, type, and sort filters.
    """
    _cleanup_expired_reservations()
    now = timezone.now()

    params = _parse_filter_params(request)
    item_type = params['item_type']
    category = params['category']
    q = params['q']
    provider_slug = params['provider_slug']
    parsed_min_price = params['min_price']
    parsed_max_price = params['max_price']
    sort = params['sort']

    events_qs = Event.objects.filter(is_active=True, start_time__gt=now).select_related('provider').prefetch_related(
        'tickets', 'tickets__resale_listing', 'tickets__resale_listing__seller'
    )

    if category and category != 'all':
        events_qs = events_qs.filter(category=category)
    if provider_slug:
        events_qs = events_qs.filter(provider__slug=provider_slug)
    if q:
        events_qs = events_qs.filter(
            Q(title__icontains=q) |
            Q(venue_or_route__icontains=q) |
            Q(provider__name__icontains=q) |
            Q(itinerary_or_details__icontains=q) |
            Q(tickets__seat_or_coach__icontains=q)
        ).distinct()

    results = []

    for ev in events_qs:
        all_tickets = list(ev.tickets.all())

        # Direct available tickets
        direct_available = [
            t for t in all_tickets
            if t.owner_id is None and (
                t.status == 'AVAILABLE' or
                (t.status == 'RESERVED' and t.reserved_until and t.reserved_until < now)
            )
        ]

        # Resale available tickets
        resale_available = [
            t for t in all_tickets
            if t.status in ('LISTED', 'RESERVED') and hasattr(t, 'resale_listing') and t.resale_listing is not None and (
                t.resale_listing.status == 'ACTIVE' or
                (t.resale_listing.status == 'RESERVED' and t.resale_listing.reserved_until and t.resale_listing.reserved_until < now)
            )
        ]

        direct_count = len(direct_available)
        resale_count = len(resale_available)
        total_available = direct_count + resale_count

        # Filter by inventory type toggle if requested
        if item_type == 'direct' and direct_count == 0:
            continue
        if item_type == 'resale' and resale_count == 0:
            continue

        # Starting price based on available direct-booking tickets
        if direct_available:
            starting_price = min(t.face_value for t in direct_available)
        elif resale_available:
            starting_price = min(t.resale_listing.total_buyer_price for t in resale_available)
        else:
            starting_price = ev.base_price

        # Price filters
        if parsed_min_price is not None and starting_price < parsed_min_price:
            continue
        if parsed_max_price is not None and starting_price > parsed_max_price:
            continue

        pr = ev.provider
        first_resale = resale_available[0] if resale_available else None
        resale_items = [
            {
                'id': f"res-{t.resale_listing.id}",
                'seat_or_coach': t.seat_or_coach,
                'price': str(t.resale_listing.total_buyer_price),
                'seller_username': t.resale_listing.seller.username,
            }
            for t in resale_available
        ]

        # Factual dates and times
        formatted_date_only = ev.start_time.strftime("%b %d, %Y")
        formatted_time = ev.start_time.strftime("%I:%M %p")
        formatted_end_time = ev.end_time.strftime("%I:%M %p") if ev.end_time else None

        # Factual seat / tier allocation
        first_ticket = first_resale if first_resale else (direct_available[0] if direct_available else None)
        seat_tier_display = first_ticket.seat_or_coach if first_ticket else 'General Entry'

        # Factual issuer / seller
        if first_resale and direct_count == 0:
            seller_or_issuer = f"@{first_resale.resale_listing.seller.username}"
            is_resale_only = True
        else:
            seller_or_issuer = pr.name
            is_resale_only = False

        # Factual inventory status
        if total_available > 0:
            status_display = 'Verified Resale' if resale_count > 0 and direct_count == 0 else 'Available'
        else:
            status_display = 'Sold Out'

        # Factual category-specific metadata
        transit_data = _parse_transit_info(ev) if ev.category in ('flight', 'train') else {}
        non_transit_data = _parse_event_details(ev) if ev.category not in ('flight', 'train') else {}

        results.append({
            'id': ev.id,
            'event_id': ev.id,
            'title': ev.title,
            'category': ev.category,
            'provider_name': pr.name,
            'provider_logo': pr.logo_url,
            'venue_or_route': ev.venue_or_route,
            'start_time': ev.start_time.isoformat(),
            'end_time': ev.end_time.isoformat() if ev.end_time else None,
            'event_date': ev.start_time.isoformat(),
            'formatted_date': ev.start_time.strftime("%b %d, %Y, %I:%M %p"),
            'formatted_date_only': formatted_date_only,
            'formatted_time': formatted_time,
            'formatted_end_time': formatted_end_time,
            'seat_tier_display': seat_tier_display,
            'seller_or_issuer': seller_or_issuer,
            'is_resale_only': is_resale_only,
            'status_display': status_display,
            'transit': transit_data,
            'venue_access': non_transit_data.get('venue_access', ''),
            'venue_tag': non_transit_data.get('venue_tag', ''),
            'itinerary_or_details': ev.itinerary_or_details,
            'image_url': ev.image_url,
            'available_tickets_count': direct_count,
            'resale_tickets_count': resale_count,
            'total_available_count': total_available,
            'starting_price': str(starting_price),
            'base_price': str(ev.base_price),
            'is_direct': True,
            'first_resale_id': f"res-{first_resale.resale_listing.id}" if first_resale else None,
            'first_resale_seat': first_resale.seat_or_coach if first_resale else None,
            'first_resale_price': str(first_resale.resale_listing.total_buyer_price) if first_resale else None,
            'resale_items': resale_items,
        })

    # Sorting
    if sort == 'price_asc':
        results.sort(key=lambda x: Decimal(x['starting_price']))
    elif sort == 'price_desc':
        results.sort(key=lambda x: Decimal(x['starting_price']), reverse=True)
    elif sort == 'date_desc':
        results.sort(key=lambda x: x['event_date'], reverse=True)
    else:
        results.sort(key=lambda x: x['event_date'])

    return results


def _format_ticket_option(t, user=None, now=None):
    """Formats an individual ticket into a structured seat selection option."""
    if now is None:
        now = timezone.now()

    is_resale = hasattr(t, 'resale_listing') and t.status in ('LISTED', 'RESERVED') and t.resale_listing is not None

    if t.event.start_time <= now:
        r = getattr(t, 'resale_listing', None) if is_resale else None
        return {
            'id': f"res-{r.id}" if (is_resale and r) else f"dir-{t.id}",
            'item_id': r.id if (is_resale and r) else t.id,
            'ticket_id': t.id,
            'ticket_ref': t.ticket_ref,
            'seat_or_coach': t.seat_or_coach,
            'price': str(r.total_buyer_price if (is_resale and r) else t.face_value),
            'asking_price': str(r.asking_price if (is_resale and r) else t.face_value),
            'service_fee': str(r.service_fee if (is_resale and r) else '0.00'),
            'face_value': str(t.face_value),
            'item_type': 'RESALE' if is_resale else 'DIRECT',
            'is_direct': not is_resale,
            'badge_text': 'Verified Resale' if is_resale else 'Direct Booking',
            'seller_username': r.seller.username if (is_resale and r) else None,
            'is_available': False,
            'status': 'EVENT_ENDED',
        }

    if is_resale:
        r = t.resale_listing
        is_avail = (
            r.status == 'ACTIVE' or
            (r.status == 'RESERVED' and r.reserved_until and r.reserved_until < now) or
            (user and user.is_authenticated and r.status == 'RESERVED' and r.reserved_by == user and r.reserved_until and r.reserved_until >= now)
        )
        return {
            'id': f"res-{r.id}",
            'item_id': r.id,
            'ticket_id': t.id,
            'ticket_ref': t.ticket_ref,
            'seat_or_coach': t.seat_or_coach,
            'price': str(r.total_buyer_price),
            'asking_price': str(r.asking_price),
            'service_fee': str(r.service_fee),
            'total_price': str(r.total_buyer_price),
            'face_value': str(t.face_value),
            'item_type': 'RESALE',
            'is_direct': False,
            'badge_text': 'Verified Resale',
            'seller_username': r.seller.username,
            'is_available': is_avail,
            'status': r.status,
        }
    else:
        is_avail = (
            t.owner_id is None and (
                t.status == 'AVAILABLE' or
                (t.status == 'RESERVED' and t.reserved_until and t.reserved_until < now) or
                (user and user.is_authenticated and t.status == 'RESERVED' and t.reserved_by == user and t.reserved_until and t.reserved_until >= now)
            )
        )
        if is_avail:
            display_status = 'AVAILABLE'
        elif t.status == 'RESERVED' and t.reserved_until and t.reserved_until >= now:
            display_status = 'RESERVED'
        elif t.owner_id is not None or t.status in ('ACTIVE', 'USED'):
            display_status = 'SOLD'
        else:
            display_status = t.status

        return {
            'id': f"dir-{t.id}",
            'item_id': t.id,
            'ticket_id': t.id,
            'ticket_ref': t.ticket_ref,
            'seat_or_coach': t.seat_or_coach,
            'price': str(t.face_value),
            'asking_price': str(t.face_value),
            'service_fee': '0.00',
            'total_price': str(t.face_value),
            'face_value': str(t.face_value),
            'item_type': 'DIRECT',
            'is_direct': True,
            'badge_text': 'Direct Booking',
            'seller_username': None,
            'is_available': is_avail,
            'status': display_status,
        }


def _resolve_listing(pk, user=None):
    """
    Resolves a raw listing identifier (res-<id>, dir-<id>, ev-<id>, or numeric)
    to a formatted listing dictionary with event metadata, available options,
    and a cinema-style seating grid parsed from actual ticket records.
    Reused across DRF API endpoints, DTL event detail, and checkout views.
    """
    _cleanup_expired_reservations()
    now = timezone.now()

    pk_str = str(pk).strip()
    target_event = None
    target_ticket = None
    target_resale = None

    if pk_str.startswith('res-'):
        try:
            target_resale = ResaleListing.objects.select_related(
                'ticket', 'ticket__event', 'ticket__event__provider', 'seller'
            ).get(pk=int(pk_str[4:]))
            target_ticket = target_resale.ticket
            target_event = target_ticket.event
        except (ValueError, ResaleListing.DoesNotExist):
            return None

    elif pk_str.startswith('dir-'):
        try:
            target_ticket = Ticket.objects.select_related('event', 'event__provider').get(pk=int(pk_str[4:]))
            target_event = target_ticket.event
        except (ValueError, Ticket.DoesNotExist):
            return None

    elif pk_str.startswith('ev-') or pk_str.startswith('event-'):
        prefix_len = 3 if pk_str.startswith('ev-') else 6
        try:
            target_event = Event.objects.select_related('provider').get(pk=int(pk_str[prefix_len:]))
        except (ValueError, Event.DoesNotExist):
            return None

    else:
        try:
            num = int(pk_str)
        except ValueError:
            return None

        # Check in order: Event, ResaleListing, Ticket
        target_event = Event.objects.select_related('provider').filter(pk=num).first()
        if not target_event:
            target_resale = ResaleListing.objects.select_related(
                'ticket', 'ticket__event', 'ticket__event__provider', 'seller'
            ).filter(pk=num).first()
            if target_resale:
                target_ticket = target_resale.ticket
                target_event = target_ticket.event
            else:
                target_ticket = Ticket.objects.select_related('event', 'event__provider').filter(pk=num).first()
                if target_ticket:
                    target_event = target_ticket.event
                else:
                    return None

    if not target_event:
        return None

    # Load all tickets for this event
    all_tickets = list(
        target_event.tickets.select_related('owner', 'reserved_by')
        .prefetch_related('resale_listing', 'resale_listing__seller')
        .order_by('face_value', 'id')
    )

    available_tickets = []
    formatted_options = []

    for t in all_tickets:
        opt = _format_ticket_option(t, user=user, now=now)
        formatted_options.append(opt)
        if opt['is_available']:
            available_tickets.append(opt)

    # Determine selected ticket:
    # If a specific ticket or resale was requested, select it.
    # If only an event was requested, leave selected_option = None so the user must select a seat.
    selected_option = None
    if target_resale:
        for opt in available_tickets:
            if opt['id'] == f"res-{target_resale.id}":
                selected_option = opt
                break
        if not selected_option:
            selected_option = _format_ticket_option(target_ticket, user=user, now=now)
    elif target_ticket:
        for opt in available_tickets:
            if opt['id'] == f"dir-{target_ticket.id}":
                selected_option = opt
                break
        if not selected_option:
            selected_option = _format_ticket_option(target_ticket, user=user, now=now)

    # Cinema seat parser: extract row label and seat number
    # Matches patterns like:
    # - "Audi 1 Laser IMAX • Recliner Row H - Seat 7 (Prime Center)" -> Row: H, Seat: 7
    # - "Screen 2 IMAX • Classic Row D - Seat 8" -> Row: D, Seat: 8
    # - "South Stand Level 2 • Bay 18 Row C Seat 12" -> Row: C, Seat: 12
    # - "Row B Seat 15" -> Row: B, Seat: 15
    seat_pattern = re.compile(
        r'\bRow\s+([A-Za-z0-9]+)\b(?:[^\d]*?\bSeat\b)?\s*[-–•,]?\s*(\d+)\b',
        re.IGNORECASE
    )

    grid_by_row = {}
    other_tickets = []

    for opt in formatted_options:
        seat_str = opt['seat_or_coach']
        match = seat_pattern.search(seat_str)
        if match:
            row_label = match.group(1).upper()
            seat_num = int(match.group(2))
            seat_info = dict(opt)
            seat_info['row'] = row_label
            seat_info['seat_number'] = seat_num
            seat_info['short_label'] = f"{row_label}{seat_num}"
            seat_info['is_selected'] = bool(selected_option and selected_option['id'] == opt['id'])
            if row_label not in grid_by_row:
                grid_by_row[row_label] = []
            grid_by_row[row_label].append(seat_info)
        else:
            # If label cannot be parsed reliably, place in other available tickets section
            if opt['is_available']:
                opt_copy = dict(opt)
                opt_copy['is_selected'] = bool(selected_option and selected_option['id'] == opt['id'])
                other_tickets.append(opt_copy)

    # Sort seats in each row by seat_number ascending
    for r_label in grid_by_row:
        grid_by_row[r_label].sort(key=lambda s: s['seat_number'])

    # Sort rows alphabetically or numerically
    sorted_row_labels = sorted(
        grid_by_row.keys(),
        key=lambda r: (0, int(r)) if r.isdigit() else (1, r)
    )

    grid_rows = [
        {
            'row_label': r,
            'seats': grid_by_row[r]
        }
        for r in sorted_row_labels
    ]

    pr = target_event.provider
    is_concluded = bool(target_event.start_time <= now)

    if is_concluded:
        selected_option = None
        chosen_avail = False
        chosen_status = 'EVENT_ENDED'
        chosen_seat = "Event concluded"
    elif selected_option:
        chosen_id = selected_option['id']
        chosen_seat = selected_option['seat_or_coach']
        chosen_price = selected_option['price']
        chosen_asking = selected_option.get('asking_price', chosen_price)
        chosen_fee = selected_option.get('service_fee', '0.00')
        chosen_face = selected_option.get('face_value', chosen_price)
        chosen_ref = selected_option['ticket_ref']
        chosen_type = selected_option['item_type']
        chosen_direct = selected_option['is_direct']
        chosen_seller = selected_option.get('seller_username')
        chosen_avail = selected_option.get('is_available', True)
        chosen_status = selected_option.get('status', 'ACTIVE')
        ticket_id = selected_option.get('ticket_id')
    else:
        chosen_avail = bool(available_tickets)
        chosen_status = 'AVAILABLE' if available_tickets else 'SOLD_OUT'
        chosen_seat = "No seat selected"

    if not selected_option:
        chosen_id = f"ev-{target_event.id}"
        chosen_price = str(target_event.base_price)
        chosen_asking = str(target_event.base_price)
        chosen_fee = '0.00'
        chosen_face = str(target_event.base_price)
        chosen_ref = 'N/A'
        chosen_type = 'DIRECT'
        chosen_direct = True
        chosen_seller = None
        ticket_id = None

    transit_info = _parse_transit_info(target_event)
    event_specs = _parse_event_details(target_event)
    raw_bullets = [b.strip() for b in (target_event.itinerary_or_details or '').split('•') if b.strip()]

    data = {
        'id': chosen_id,
        'event_id': target_event.id,
        'title': target_event.title,
        'category': target_event.category,
        'provider_name': pr.name,
        'provider_logo': pr.logo_url,
        'venue_or_route': target_event.venue_or_route,
        'transit_info': transit_info,
        'event_specs': event_specs,
        'spec_bullets': raw_bullets,
        'start_time': target_event.start_time.isoformat(),
        'end_time': target_event.end_time.isoformat() if target_event.end_time else None,
        'event_date': target_event.start_time.isoformat(),
        'formatted_date': target_event.start_time.strftime("%b %d, %Y, %I:%M %p"),
        'formatted_date_only': target_event.start_time.strftime("%A, %d %B") if target_event.start_time else '',
        'formatted_time_only': target_event.start_time.strftime("%I:%M %p") if target_event.start_time else '',
        'itinerary_or_details': target_event.itinerary_or_details,
        'image_url': target_event.image_url,
        'base_price': str(target_event.base_price),
        'is_concluded': is_concluded,

        # Selected ticket / checkout properties
        'ticket_id': ticket_id,
        'ticket_ref': chosen_ref,
        'seat_or_coach': chosen_seat,
        'price': chosen_price,
        'asking_price': chosen_asking,
        'service_fee': chosen_fee,
        'total_price': chosen_price,
        'face_value': chosen_face,
        'item_type': chosen_type,
        'is_direct': chosen_direct,
        'seller_username': chosen_seller,
        'is_available': chosen_avail,
        'status': chosen_status,

        # Seat inventory and seating grid
        'available_tickets': available_tickets,
        'selected_ticket': selected_option,
        'available_count': len(available_tickets),
        'resale_tickets': [opt for opt in available_tickets if not opt['is_direct']],
        'has_resale_tickets': any(not opt['is_direct'] for opt in available_tickets),
        'direct_tickets': [opt for opt in available_tickets if opt['is_direct']],
        'has_direct_tickets': any(opt['is_direct'] for opt in available_tickets),
        'is_resale_target': bool(target_resale is not None),
        'grid_rows': grid_rows,
        'has_grid': bool(grid_rows),
        'other_tickets': other_tickets,
        'has_other_tickets': bool(other_tickets),
    }
    return data


@api_view(['GET'])
@permission_classes([AllowAny])
def get_listing_detail(request, pk):
    """
    Retrieves details for a specific listing (either dir-<id>, res-<id>, or numeric id).
    """
    listing = _resolve_listing(pk, user=request.user)
    if not listing:
        return Response({'error': 'Listing not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response({'listing': listing})


def _execute_create_resale_listing(user, ticket_id, asking_price_raw=None):
    """
    Authoritative business logic for listing an owned ticket for peer-to-peer resale.
    Sellers receive 70% to 80% (standard 80%) of the original ticket face value.
    Returns (listing, error_message, error_code, max_allowed).
    """
    if not user.is_identity_verified or not user.verified_legal_name:
        return None, 'Identity verification required before listing tickets.', 403, None

    if not ticket_id:
        return None, 'ticket_id is required.', 400, None

    with transaction.atomic():
        try:
            ticket = Ticket.objects.select_for_update().select_related('event').get(pk=ticket_id, owner=user)
        except (Ticket.DoesNotExist, ValueError):
            return None, 'Ticket not found or not owned by you.', 404, None

        if ticket.status != 'ACTIVE':
            return None, f"Ticket cannot be listed because its status is '{ticket.status}'. Only active tickets can be resold.", 400, None

        if ticket.event.start_time <= timezone.now():
            return None, 'This event has already started or concluded. Expired tickets cannot be listed.', 400, None

        # Standard payout: 80% of original ticket face value (70%-80% policy)
        default_payout = (ticket.face_value * Decimal('0.80')).quantize(Decimal('0.01'))

        if asking_price_raw is not None and str(asking_price_raw).strip() != '':
            try:
                asking_price = Decimal(str(asking_price_raw).strip())
                if not asking_price.is_finite() or asking_price <= 0:
                    raise ValueError()
            except (InvalidOperation, TypeError, ValueError):
                return None, 'Please provide a valid positive asking price.', 400, None
        else:
            asking_price = default_payout

        # Platform anti-scalping rule: asking price capped at 120% of face value
        max_allowed = (ticket.face_value * Decimal('1.20')).quantize(Decimal('0.01'))
        if asking_price > max_allowed:
            return None, f'Asking price exceeds the platform maximum allowed resale price of ₹{max_allowed} (120% of face value).', 400, max_allowed

        # Buyer pays 120% of original face value. Platform fee is the difference ensuring platform profitability (120% buyer price - 80% seller payout = 40% platform margin)
        total_buyer_target = (ticket.face_value * Decimal('1.20')).quantize(Decimal('0.01'))
        calculated_service_fee = max(total_buyer_target - asking_price, (ticket.face_value * Decimal('0.20'))).quantize(Decimal('0.01'))

        # Check if already has an active or reserved resale listing
        existing = ResaleListing.objects.filter(ticket=ticket).first()
        if existing and existing.status in ('ACTIVE', 'RESERVED'):
            return None, f'A resale listing already exists for this ticket in status {existing.status}.', 409, None

        if existing:
            listing = existing
            listing.seller = user
            listing.asking_price = asking_price
            listing.service_fee = calculated_service_fee
            listing.status = 'ACTIVE'
            listing.reserved_by = None
            listing.reserved_until = None
            listing.save()
        else:
            listing = ResaleListing.objects.create(
                ticket=ticket,
                seller=user,
                asking_price=asking_price,
                service_fee=calculated_service_fee,
                status='ACTIVE'
            )

        ticket.status = 'LISTED'
        ticket.save(update_fields=['status'])

    return listing, None, 201, None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_resale_listing(request):
    """
    List an owned ticket for peer-to-peer resale.
    Strictly enforces:
    1. Seller must be KYC verified via Setu DigiLocker.
    2. Ticket must be owned by the seller.
    3. Ticket status must be ACTIVE.
    4. Event date must be in the future.
    5. Asking price must follow platform rules (<= 120% of face value).
    """
    ticket_id = request.data.get('ticket_id')
    asking_price_raw = request.data.get('asking_price')

    listing, error, code, max_allowed = _execute_create_resale_listing(request.user, ticket_id, asking_price_raw)
    if error:
        if code == 403:
            return Response({
                'error': error,
                'code': 'KYC_REQUIRED',
                'detail': 'You must complete verified DigiLocker KYC before you can list tickets for resale.'
            }, status=status.HTTP_403_FORBIDDEN)
        if max_allowed is not None:
            return Response({'error': error, 'max_allowed_price': str(max_allowed)}, status=status.HTTP_400_BAD_REQUEST)
        status_map = {400: status.HTTP_400_BAD_REQUEST, 404: status.HTTP_404_NOT_FOUND, 409: status.HTTP_409_CONFLICT}
        return Response({'error': error}, status=status_map.get(code, status.HTTP_400_BAD_REQUEST))

    ticket = listing.ticket
    return Response({
        'success': True,
        'message': 'Ticket listed successfully for peer-to-peer resale.',
        'listing': {
            'id': f"res-{listing.id}",
            'listing_id': listing.id,
            'ticket_id': ticket.id,
            'ticket_ref': ticket.ticket_ref,
            'title': ticket.event.title,
            'asking_price': str(listing.asking_price),
            'service_fee': str(listing.service_fee),
            'total_price': str(listing.total_buyer_price),
            'status': listing.status,
            'created_at': listing.created_at.isoformat(),
        }
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_seller_listings(request):
    """Returns all resale listings created by the authenticated user."""
    _cleanup_expired_reservations()

    listings = ResaleListing.objects.filter(
        seller=request.user
    ).select_related('ticket', 'ticket__event', 'ticket__event__provider').order_by('-created_at')

    results = []
    for r in listings:
        t = r.ticket
        ev = t.event
        pr = ev.provider
        results.append({
            'id': r.id,
            'listing_id': r.id,
            'ticket_id': t.id,
            'ticket_ref': t.ticket_ref,
            'title': ev.title,
            'category': ev.category,
            'provider_name': pr.name,
            'venue_or_route': ev.venue_or_route,
            'event_date': ev.start_time.isoformat(),
            'seat_or_coach': t.seat_or_coach,
            'face_value': str(t.face_value),
            'asking_price': str(r.asking_price),
            'service_fee': str(r.service_fee),
            'total_price': str(r.total_buyer_price),
            'status': r.status,
            'created_at': r.created_at.isoformat(),
        })

    return Response({'listings': results})


def _execute_withdraw_listing(user, pk):
    """
    Authoritative withdrawal logic for an active resale listing.
    Transitions listing to WITHDRAWN and associated ticket back to ACTIVE.
    """
    pk_str = str(pk).strip()
    if pk_str.startswith('res-'):
        pk_str = pk_str[4:]

    with transaction.atomic():
        try:
            listing = ResaleListing.objects.select_for_update().select_related('ticket').get(
                pk=pk_str, seller=user
            )
        except (ResaleListing.DoesNotExist, ValueError):
            return None, 'Listing not found or not owned by you.', 404

        if listing.status != 'ACTIVE':
            return None, f"Cannot withdraw listing in status '{listing.status}'.", 400

        listing.status = 'WITHDRAWN'
        listing.save(update_fields=['status'])

        ticket = listing.ticket
        if ticket.status == 'LISTED':
            ticket.status = 'ACTIVE'
            ticket.save(update_fields=['status'])

    return listing, None, 200


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def withdraw_resale_listing(request, pk):
    """Withdraw an active resale listing and return ticket to active vault status."""
    listing, error, code = _execute_withdraw_listing(request.user, pk)
    if error:
        return Response({'error': error}, status=status.HTTP_404_NOT_FOUND if code == 404 else status.HTTP_400_BAD_REQUEST)

    return Response({
        'success': True,
        'message': 'Listing withdrawn successfully. Your ticket is now back in your vault.'
    })


# ==============================================================================
# Traditional Django Template Web Views
# ==============================================================================

@require_http_methods(['GET'])
def explore_view(request):
    """
    Explore page for event and ticket listing discovery.
    Displays one card per event with available direct-booking ticket counts and starting prices.
    Preserves category, search, provider, and sort filters.
    Provides real-time platform metrics and authenticated user account data.
    """
    events = _get_event_listings(request)

    # Real platform metrics from database
    total_resales = ResaleListing.objects.filter(status='ACTIVE').count()
    total_direct = Ticket.objects.filter(status='AVAILABLE', owner__isnull=True).count()
    total_providers = Provider.objects.filter(is_active=True).count()

    # Real user stats
    user_vault_count = 0
    user_listings = []
    wallet_balance = Decimal('0.00')

    if request.user.is_authenticated:
        user_vault_count = Ticket.objects.filter(owner=request.user, status__in=['ACTIVE', 'LISTED']).count()
        user_listings = (
            ResaleListing.objects.filter(seller=request.user)
            .select_related('ticket', 'ticket__event', 'ticket__event__provider')
            .order_by('-created_at')[:5]
        )
        wallet = request.user.get_wallet()
        wallet_balance = wallet.balance

    context = {
        'categories': CATEGORIES,
        'events': events,
        'selected_type': request.GET.get('type', 'all').lower(),
        'selected_category': request.GET.get('category', '').strip().lower() or 'all',
        'search_query': request.GET.get('q', '').strip(),
        'sort_by': request.GET.get('sort', 'date_asc'),
        'total_count': len(events),
        'total_resales': total_resales,
        'total_direct': total_direct,
        'total_providers': total_providers,
        'user_vault_count': user_vault_count,
        'user_listings': user_listings,
        'wallet_balance': wallet_balance,
    }
    return render(request, 'catalog/explore.html', context)


@require_http_methods(['GET'])
def listing_detail_view(request, pk):
    """
    Displays the details of an event and its available seats for selection.
    Reuses authoritative listing resolution from catalog/views.py.
    """
    listing = _resolve_listing(pk, user=request.user)
    if not listing:
        raise Http404('Event or listing not found.')

    available_tickets = listing.get('available_tickets', [])
    selected_ticket = listing.get('selected_ticket')

    # Allow query parameter override for selected seat (?seat=<id>)
    seat_override = request.GET.get('seat', '').strip()
    if seat_override:
        for opt in available_tickets:
            if opt['id'] == seat_override or str(opt.get('ticket_id')) == seat_override:
                selected_ticket = opt
                listing['id'] = opt['id']
                listing['ticket_id'] = opt.get('ticket_id')
                listing['ticket_ref'] = opt['ticket_ref']
                listing['seat_or_coach'] = opt['seat_or_coach']
                listing['price'] = opt['price']
                listing['total_price'] = opt['price']
                listing['is_direct'] = opt['is_direct']
                listing['seller_username'] = opt.get('seller_username')
                break

    # If seat was selected (via pk or query parameter), ensure is_selected is updated in grid_rows and other_tickets
    if selected_ticket:
        for row in listing.get('grid_rows', []):
            for s in row['seats']:
                s['is_selected'] = (s['id'] == selected_ticket['id'])
        for s in listing.get('other_tickets', []):
            s['is_selected'] = (s['id'] == selected_ticket['id'])

    is_owner = (
        request.user.is_authenticated and
        selected_ticket and
        selected_ticket.get('seller_username') and
        request.user.username == selected_ticket['seller_username']
    )
    context = {
        'listing': listing,
        'event': listing,
        'available_tickets': available_tickets,
        'selected_ticket': selected_ticket,
        'grid_rows': listing.get('grid_rows', []),
        'has_grid': listing.get('has_grid', False),
        'other_tickets': listing.get('other_tickets', []),
        'has_other_tickets': listing.get('has_other_tickets', False),
        'resale_tickets': listing.get('resale_tickets', []),
        'has_resale_tickets': listing.get('has_resale_tickets', False),
        'direct_tickets': listing.get('direct_tickets', []),
        'has_direct_tickets': listing.get('has_direct_tickets', False),
        'is_resale_target': listing.get('is_resale_target', False) or bool(selected_ticket and not selected_ticket.get('is_direct')),
        'is_resale_selected': bool(selected_ticket and not selected_ticket.get('is_direct')),
        'is_owner': is_owner,
        'is_concluded': listing.get('is_concluded', False),
        'is_available': bool(available_tickets) and not listing.get('is_concluded', False),
        'total_available_count': len(available_tickets),
    }
    return render(request, 'catalog/listing_detail.html', context)


@login_required
@require_http_methods(['GET', 'POST'])
def sell_ticket_view(request):
    """
    Sell ticket page: select an active owned ticket and publish for peer-to-peer resale.
    Reuses authoritative listing creation logic from catalog/views.py.
    """
    is_kyc_verified = bool(request.user.is_identity_verified and request.user.verified_legal_name)

    eligible_tickets = []
    if is_kyc_verified:
        eligible_tickets = Ticket.objects.filter(
            owner=request.user,
            status='ACTIVE',
            event__start_time__gt=timezone.now()
        ).select_related('event', 'event__provider').order_by('event__start_time')

    formatted_tickets = [
        {
            'id': t.id,
            'ticket_ref': t.ticket_ref,
            'booking_reference': t.booking_reference,
            'title': t.event.title,
            'category': t.event.category,
            'provider_name': t.event.provider.name,
            'venue_or_route': t.event.venue_or_route,
            'seat_or_coach': t.seat_or_coach,
            'formatted_date': t.event.start_time.strftime("%b %d, %Y, %I:%M %p"),
            'face_value': str(t.face_value),
            'payout_amount': str((t.face_value * Decimal('0.80')).quantize(Decimal('0.01'))),
            'buyer_price': str((t.face_value * Decimal('1.20')).quantize(Decimal('0.01'))),
            'max_allowed_resale_price': str((t.face_value * Decimal('1.20')).quantize(Decimal('0.01'))),
            'attendee_name': t.attendee_name,
        }
        for t in eligible_tickets
    ]

    if request.method == 'POST':
        ticket_id = request.POST.get('ticket_id')
        asking_price_raw = request.POST.get('asking_price')

        _, error, code, max_allowed = _execute_create_resale_listing(
            request.user, ticket_id, asking_price_raw
        )

        if error:
            messages.error(request, error)
            if code == 400 and max_allowed is not None:
                return render(request, 'catalog/sell.html', {
                    'is_kyc_verified': is_kyc_verified,
                    'eligible_tickets': formatted_tickets,
                    'selected_ticket_id': ticket_id,
                    'asking_price': asking_price_raw,
                })
            return redirect('sell-ticket' if code != 409 else 'my-listings')

        messages.success(request, 'Ticket listed successfully for peer-to-peer resale.')
        return redirect('my-listings')

    return render(request, 'catalog/sell.html', {
        'is_kyc_verified': is_kyc_verified,
        'eligible_tickets': formatted_tickets,
        'selected_ticket_id': request.GET.get('ticketId', ''),
    })


@login_required
@require_http_methods(['GET'])
def my_listings_view(request):
    """
    Displays all resale listings created by the authenticated user.
    """
    _cleanup_expired_reservations()
    listings = list(ResaleListing.objects.filter(
        seller=request.user
    ).select_related('ticket', 'ticket__event', 'ticket__event__provider').order_by('-created_at'))

    active_count = 0
    reserved_count = 0
    sold_count = 0

    for item in listings:
        if item.status == 'ACTIVE':
            active_count += 1
        elif item.status == 'RESERVED':
            reserved_count += 1
        elif item.status == 'SOLD':
            sold_count += 1

        ev = item.ticket.event
        if ev.category in ('train', 'flight'):
            item.transit = _parse_transit_info(ev)
        else:
            item.transit = None

    context = {
        'listings': listings,
        'active_count': active_count,
        'reserved_count': reserved_count,
        'sold_count': sold_count,
        'total_count': len(listings),
    }
    return render(request, 'catalog/my_listings.html', context)


@login_required
@require_POST
def withdraw_listing_view(request, pk):
    """
    Withdraw an active resale listing and return the ticket to active vault status.
    Reuses authoritative withdrawal logic from catalog/views.py.
    """
    _, error, code = _execute_withdraw_listing(request.user, pk)
    if code == 404:
        raise Http404(error)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, 'Listing withdrawn successfully. Your ticket has been returned to your vault.')
    return redirect('my-listings')

