import hmac
import hashlib
import uuid
import razorpay
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Order, Payment
from ticketing.models import Ticket, ResaleListing
from accounts.models import Wallet, WalletTransaction
import json
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from catalog.views import _resolve_listing


def get_razorpay_client():
    """Initializes and returns authenticated Razorpay API client."""
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def _execute_checkout_order(user, raw_id, attendee_name=''):
    """
    Authoritative business logic for checkout and inventory reservation.
    1. Validates Setu DigiLocker KYC identity verification.
    2. Atomically locks inventory with select_for_update().
    3. Reserves inventory for 10 minutes.
    4. Creates pending Order and Razorpay order.
    Returns: (order_dict, razorpay_dict, error_dict, http_status_code)
    """
    if not user.is_identity_verified or not user.verified_legal_name:
        return None, None, {
            'error': 'Identity verification required.',
            'code': 'KYC_REQUIRED',
            'detail': 'You must complete genuine Setu DigiLocker identity verification before booking or purchasing tickets on this platform.'
        }, 403

    raw_id = str(raw_id or '').strip()
    attendee_name = (attendee_name or '').strip()

    if not raw_id:
        return None, None, {'error': 'Listing or ticket ID is required.'}, 400

    is_resale = False
    is_direct = False
    item_id = None

    try:
        if raw_id.startswith('res-'):
            is_resale = True
            item_id = int(raw_id[4:])
        elif raw_id.startswith('dir-'):
            is_direct = True
            item_id = int(raw_id[4:])
        else:
            num = int(raw_id)
            if ResaleListing.objects.filter(pk=num).exists():
                is_resale = True
                item_id = num
            elif Ticket.objects.filter(pk=num).exists():
                is_direct = True
                item_id = num
    except ValueError:
        return None, None, {'error': 'Invalid inventory identifier.'}, 400

    now = timezone.now()
    reservation_window = now + timezone.timedelta(minutes=10)
    attendee = attendee_name or user.verified_legal_name or user.username

    with transaction.atomic():
        if is_resale:
            try:
                resale_listing = ResaleListing.objects.select_for_update().select_related('ticket', 'ticket__event', 'seller').get(pk=item_id)
            except ResaleListing.DoesNotExist:
                return None, None, {'error': 'Resale listing not found.'}, 404

            # Check event expiry
            if resale_listing.ticket.event.start_time <= now:
                return None, None, {
                    'error': 'This event has already started or concluded and tickets can no longer be purchased.',
                    'code': 'EVENT_EXPIRED'
                }, 400

            # Verify seller identity verification
            if not resale_listing.seller.is_identity_verified or not resale_listing.seller.verified_legal_name:
                return None, None, {
                    'error': 'Seller identity verification is invalid or incomplete.',
                    'code': 'SELLER_KYC_REQUIRED'
                }, 403

            # Prevent seller from purchasing their own ticket
            if resale_listing.seller == user:
                return None, None, {
                    'error': 'Sellers are not permitted to purchase their own ticket listings.'
                }, 400

            # Check availability
            is_free = (
                resale_listing.status == 'ACTIVE' or
                (resale_listing.status == 'RESERVED' and resale_listing.reserved_until and resale_listing.reserved_until < now)
            )
            is_current_buyer = (
                resale_listing.status == 'RESERVED' and
                resale_listing.reserved_by == user and
                resale_listing.reserved_until and
                resale_listing.reserved_until >= now
            )

            if not is_free and not is_current_buyer:
                return None, None, {
                    'error': 'This ticket listing is currently reserved by another buyer or has been sold.',
                    'code': 'LISTING_UNAVAILABLE',
                    'status': resale_listing.status
                }, 409

            # Reserve listing & ticket
            resale_listing.status = 'RESERVED'
            resale_listing.reserved_by = user
            resale_listing.reserved_until = reservation_window
            resale_listing.save(update_fields=['status', 'reserved_by', 'reserved_until'])

            ticket = resale_listing.ticket
            ticket.status = 'RESERVED'
            ticket.reserved_by = user
            ticket.reserved_until = reservation_window
            ticket.save(update_fields=['status', 'reserved_by', 'reserved_until'])

            ticket_price = resale_listing.asking_price
            service_fee = resale_listing.service_fee
            total_amount = resale_listing.total_buyer_price
            order_type = 'RESALE'

        elif is_direct:
            try:
                ticket = Ticket.objects.select_for_update().select_related('event').get(pk=item_id)
            except Ticket.DoesNotExist:
                return None, None, {'error': 'Ticket not found.'}, 404

            # Check event expiry
            if ticket.event.start_time <= now:
                return None, None, {
                    'error': 'This event has already started or concluded and tickets can no longer be booked.',
                    'code': 'EVENT_EXPIRED'
                }, 400

            # Direct ticket must have no owner and be available or expired reserved
            if ticket.owner is not None:
                return None, None, {'error': 'This ticket is already owned by a user.'}, 409

            is_free = (
                ticket.status == 'AVAILABLE' or
                (ticket.status == 'RESERVED' and ticket.reserved_until and ticket.reserved_until < now)
            )
            is_current_buyer = (
                ticket.status == 'RESERVED' and
                ticket.reserved_by == user and
                ticket.reserved_until and
                ticket.reserved_until >= now
            )

            if not is_free and not is_current_buyer:
                return None, None, {
                    'error': 'This ticket is currently reserved by another buyer or has been booked.',
                    'code': 'TICKET_UNAVAILABLE',
                    'status': ticket.status
                }, 409

            ticket.status = 'RESERVED'
            ticket.reserved_by = user
            ticket.reserved_until = reservation_window
            ticket.save(update_fields=['status', 'reserved_by', 'reserved_until'])

            resale_listing = None
            ticket_price = ticket.face_value
            service_fee = Decimal('0.00')
            total_amount = ticket.face_value
            order_type = 'DIRECT'
        else:
            return None, None, {'error': 'Item not found.'}, 404

        # Check for existing pending order for this user and ticket
        order = Order.objects.filter(
            buyer=user,
            ticket=ticket,
            status='PENDING'
        ).first()

        if not order:
            order = Order.objects.create(
                buyer=user,
                order_type=order_type,
                ticket=ticket,
                resale_listing=resale_listing,
                order_number=f"ORD-{uuid.uuid4().hex[:8].upper()}",
                ticket_price=ticket_price,
                service_fee=service_fee,
                total_amount=total_amount,
                status='PENDING',
                attendee_name=attendee,
            )
        else:
            order.attendee_name = attendee
            order.ticket_price = ticket_price
            order.service_fee = service_fee
            order.total_amount = total_amount
            order.order_type = order_type
            order.resale_listing = resale_listing
            order.save()

        # Create or update Razorpay Order
        amount_in_paise = int(order.total_amount * 100)
        rzp_order_id = ''

        has_keys = bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)
        if has_keys:
            try:
                client = get_razorpay_client()
                rzp_order = client.order.create({
                    'amount': amount_in_paise,
                    'currency': 'INR',
                    'receipt': order.order_number,
                    'notes': {
                        'order_id': order.id,
                        'order_type': order_type,
                        'ticket_ref': ticket.ticket_ref,
                        'buyer_username': user.username,
                    }
                })
                rzp_order_id = rzp_order['id']
            except Exception:
                rzp_order_id = f"order_{order.order_number}"
        else:
            rzp_order_id = f"order_{order.order_number}"

        order.razorpay_order_id = rzp_order_id
        order.save(update_fields=['razorpay_order_id'])

    ev = ticket.event
    order_dict = {
        'id': order.id,
        'order_number': order.order_number,
        'order_type': order.order_type,
        'ticket_id': ticket.id,
        'ticket_ref': ticket.ticket_ref,
        'title': ev.title,
        'category': ev.category,
        'venue_or_route': ev.venue_or_route,
        'seat_or_coach': ticket.seat_or_coach,
        'event_date': ev.start_time.isoformat(),
        'ticket_price': str(order.ticket_price),
        'service_fee': str(order.service_fee),
        'total_amount': str(order.total_amount),
        'status': order.status,
        'attendee_name': order.attendee_name,
        'reservation_expires_at': reservation_window.isoformat(),
    }
    rzp_dict = {
        'order_id': rzp_order_id,
        'amount': amount_in_paise,
        'currency': 'INR',
        'key_id': settings.RAZORPAY_KEY_ID,
    }
    return order_dict, rzp_dict, None, 201


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_checkout_order(request):
    """
    Initiates checkout for either direct booking inventory or peer-to-peer resale.
    Mandatory Setu DigiLocker KYC identity verification is required before initiating any transaction.
    Locks the inventory for 10 minutes and creates a pending Order + Razorpay order.
    """
    raw_id = request.data.get('listing_id') or request.data.get('ticket_id')
    attendee_name = request.data.get('attendee_name', '')
    order_dict, rzp_dict, err, code = _execute_checkout_order(request.user, raw_id, attendee_name)
    if err:
        return Response(err, status=code)
    return Response({'order': order_dict, 'razorpay': rzp_dict}, status=status.HTTP_201_CREATED)


def _execute_verify_payment(user, order_id, razorpay_order_id, razorpay_payment_id, razorpay_signature):
    """
    Authoritative logic to cryptographically verify Razorpay payment and complete issuance/transfer.
    Returns: (result_data, error_dict, http_status_code)
    """
    if not user:
        return None, {'error': 'Authentication required.', 'code': 'UNAUTHORIZED'}, 401

    if not user.is_identity_verified or not user.verified_legal_name:
        return None, {
            'error': 'Identity verification required.',
            'code': 'KYC_REQUIRED',
            'detail': 'You must complete genuine Setu DigiLocker verification before completing ticket transactions.'
        }, 403

    razorpay_order_id = (razorpay_order_id or '').strip()
    razorpay_payment_id = (razorpay_payment_id or '').strip()
    razorpay_signature = (razorpay_signature or '').strip()

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return None, {
            'error': 'Missing required Razorpay payment confirmation fields.',
            'code': 'MISSING_PAYMENT_FIELDS'
        }, 400

    with transaction.atomic():
        try:
            # Lock order record
            order = Order.objects.select_for_update().get(pk=order_id, buyer=user)
        except Order.DoesNotExist:
            return None, {'error': 'Order not found.'}, 404

        # Idempotent response if already completed
        if order.status == 'COMPLETED':
            t = order.ticket
            return {
                'success': True,
                'message': 'Order is already completed and ticket issued.',
                'order': {
                    'order_number': order.order_number,
                    'status': order.status,
                    'total_amount': str(order.total_amount),
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_order_id': order.razorpay_order_id,
                },
                'ticket': {
                    'id': t.id if t else None,
                    'ticket_ref': t.ticket_ref if t else '',
                    'booking_reference': t.booking_reference if t else '',
                    'attendee_name': t.attendee_name if t else '',
                    'qr_token': t.qr_token if t else None,
                    'qr_image_url': t.get_qr_image_base64() if t else None,
                }
            }, None, 200

        # Idempotent response if already refunded
        if order.status == 'REFUNDED':
            return None, {
                'error': 'This order has already been refunded.',
                'code': 'ORDER_REFUNDED',
                'order_status': order.status,
            }, 409

        # Duplicate callback guard for already cancelled orders where payment was already captured
        existing_paid = Payment.objects.filter(order=order, status='PAID').first()
        if order.status == 'CANCELLED' and existing_paid:
            return None, {
                'error': 'This order was cancelled due to reservation expiry.',
                'code': 'ORDER_CANCELLED',
                'order_status': order.status,
            }, 409

        if order.status not in ('PENDING', 'CANCELLED'):
            return None, {
                'error': f"Order cannot be verified in current status '{order.status}'.",
                'code': 'INVALID_ORDER_STATUS',
                'order_status': order.status
            }, 400

        # Verify razorpay_order_id matches
        if order.razorpay_order_id and razorpay_order_id != order.razorpay_order_id:
            return None, {
                'error': 'Razorpay order ID mismatch.',
                'code': 'ORDER_ID_MISMATCH'
            }, 400

        # Cryptographic Signature Verification
        secret = settings.RAZORPAY_KEY_SECRET or ''
        body = f"{razorpay_order_id}|{razorpay_payment_id}"
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        is_valid = False
        if hmac.compare_digest(expected_signature, razorpay_signature):
            is_valid = True
        else:
            try:
                client = get_razorpay_client()
                client.utility.verify_payment_signature({
                    'razorpay_order_id': razorpay_order_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature
                })
                is_valid = True
            except Exception:
                is_valid = False

        if not is_valid:
            Payment.objects.update_or_create(
                order=order,
                defaults={
                    'razorpay_order_id': razorpay_order_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature,
                    'amount': order.total_amount,
                    'currency': 'INR',
                    'status': 'FAILED',
                    'error_code': 'INVALID_SIGNATURE',
                    'error_description': 'Cryptographic payment signature mismatch.'
                }
            )
            order.status = 'PAYMENT_FAILED'
            order.save(update_fields=['status'])
            return None, {
                'success': False,
                'error': 'Invalid payment signature. Verification failed.',
                'code': 'INVALID_SIGNATURE'
            }, 400

        # Live API verification of amount with Razorpay
        if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            try:
                client = get_razorpay_client()
                rzp_payment = client.payment.fetch(razorpay_payment_id)
                expected_paise = int(order.total_amount * 100)
                if rzp_payment.get('amount') and rzp_payment.get('amount') != expected_paise:
                    Payment.objects.update_or_create(
                        order=order,
                        defaults={
                            'razorpay_order_id': razorpay_order_id,
                            'razorpay_payment_id': razorpay_payment_id,
                            'razorpay_signature': razorpay_signature,
                            'amount': order.total_amount,
                            'currency': 'INR',
                            'status': 'FAILED',
                            'error_code': 'AMOUNT_MISMATCH',
                            'error_description': f"Captured payment amount ({rzp_payment.get('amount')} paise) does not match order total ({expected_paise} paise)."
                        }
                    )
                    order.status = 'PAYMENT_FAILED'
                    order.save(update_fields=['status'])
                    return None, {
                        'error': 'Payment amount mismatch.',
                        'code': 'AMOUNT_MISMATCH'
                    }, 400
            except Exception:
                pass

        # Lock ticket record separately to avoid outer join locking issues
        try:
            ticket = Ticket.objects.select_for_update().select_related('event', 'owner', 'reserved_by').get(pk=order.ticket_id)
        except Ticket.DoesNotExist:
            return None, {'error': 'Associated ticket not found.', 'code': 'TICKET_NOT_FOUND'}, 404

        # Lock resale listing record if applicable
        resale_listing = None
        if order.order_type == 'RESALE' and order.resale_listing_id:
            try:
                resale_listing = ResaleListing.objects.select_for_update().select_related('ticket', 'seller', 'reserved_by').get(pk=order.resale_listing_id)
            except ResaleListing.DoesNotExist:
                return None, {'error': 'Associated resale listing not found.', 'code': 'LISTING_NOT_FOUND'}, 404

        now = timezone.now()
        is_reservation_valid = True
        invalidation_reason = ''

        # If order was cancelled prior to payment confirmation
        if order.status == 'CANCELLED':
            is_reservation_valid = False
            invalidation_reason = 'Order was cancelled prior to payment confirmation.'

        elif order.order_type == 'DIRECT':
            if ticket.owner is not None:
                is_reservation_valid = False
                invalidation_reason = 'Ticket is already owned by a user.'
            elif ticket.status != 'RESERVED':
                is_reservation_valid = False
                invalidation_reason = f"Ticket status is '{ticket.status}' (expected RESERVED)."
            elif ticket.reserved_by != user:
                is_reservation_valid = False
                invalidation_reason = 'Ticket reservation is held by another user.'
            elif not ticket.reserved_until or ticket.reserved_until < now:
                is_reservation_valid = False
                invalidation_reason = 'Ticket reservation hold has expired.'

        elif order.order_type == 'RESALE':
            if not resale_listing:
                is_reservation_valid = False
                invalidation_reason = 'Resale listing record is missing.'
            elif resale_listing.status != 'RESERVED':
                is_reservation_valid = False
                invalidation_reason = f"Resale listing status is '{resale_listing.status}' (expected RESERVED)."
            elif resale_listing.reserved_by != user:
                is_reservation_valid = False
                invalidation_reason = 'Resale listing reservation is held by another user.'
            elif not resale_listing.reserved_until or resale_listing.reserved_until < now:
                is_reservation_valid = False
                invalidation_reason = 'Resale listing reservation hold has expired.'
            elif ticket.owner != resale_listing.seller:
                is_reservation_valid = False
                invalidation_reason = 'Ticket is no longer owned by the listing seller.'
            elif ticket.status != 'RESERVED' or ticket.reserved_by != user:
                is_reservation_valid = False
                invalidation_reason = 'Ticket is not reserved for this buyer.'
            elif not ticket.reserved_until or ticket.reserved_until < now:
                is_reservation_valid = False
                invalidation_reason = 'Ticket reservation hold has expired.'
            elif not resale_listing.seller.is_identity_verified or not resale_listing.seller.verified_legal_name:
                is_reservation_valid = False
                invalidation_reason = 'Seller identity verification is invalid or expired.'
            elif resale_listing.seller == user:
                is_reservation_valid = False
                invalidation_reason = 'Buyer cannot purchase their own resale listing.'
            elif Order.objects.filter(resale_listing=resale_listing, status='COMPLETED').exclude(pk=order.pk).exists():
                is_reservation_valid = False
                invalidation_reason = 'This resale listing was already completed in another transaction.'

        # --- Handle Late Payments / Expired Reservations ---
        if not is_reservation_valid:
            Payment.objects.update_or_create(
                order=order,
                defaults={
                    'razorpay_order_id': razorpay_order_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature,
                    'amount': order.total_amount,
                    'currency': 'INR',
                    'status': 'PAID',
                    'paid_at': now,
                    'error_code': 'RESERVATION_EXPIRED',
                    'error_description': f"Payment captured ({razorpay_payment_id}), but reservation was invalid: {invalidation_reason}. Safe reconciliation / manual refund required."
                }
            )
            order.status = 'CANCELLED'
            order.save(update_fields=['status'])

            # If the user still held the expired reservation and no other buyer took it,
            # release the expired hold back to available/active so inventory remains consistent
            if order.order_type == 'DIRECT' and ticket.owner is None and ticket.reserved_by == user:
                ticket.status = 'AVAILABLE'
                ticket.reserved_by = None
                ticket.reserved_until = None
                ticket.save(update_fields=['status', 'reserved_by', 'reserved_until'])
            elif order.order_type == 'RESALE':
                if resale_listing and resale_listing.reserved_by == user and resale_listing.status == 'RESERVED':
                    resale_listing.status = 'ACTIVE'
                    resale_listing.reserved_by = None
                    resale_listing.reserved_until = None
                    resale_listing.save(update_fields=['status', 'reserved_by', 'reserved_until'])
                if ticket.reserved_by == user and ticket.owner == (resale_listing.seller if resale_listing else ticket.owner) and ticket.status == 'RESERVED':
                    ticket.status = 'LISTED'
                    ticket.reserved_by = None
                    ticket.reserved_until = None
                    ticket.save(update_fields=['status', 'reserved_by', 'reserved_until'])

            return None, {
                'error': f"Reservation expired or ticket no longer available: {invalidation_reason}",
                'code': 'RESERVATION_EXPIRED',
                'order_id': order.id,
                'order_number': order.order_number,
                'order_status': order.status,
                'razorpay_payment_id': razorpay_payment_id,
                'payment_status': 'PAID',
                'detail': 'Your payment was received at the gateway, but the ticket reservation could not be completed. The transaction has been recorded for reconciliation.'
            }, 409

        # --- Reservation IS VALID: Proceed with issuance / transfer ---
        Payment.objects.update_or_create(
            order=order,
            defaults={
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature,
                'amount': order.total_amount,
                'currency': 'INR',
                'status': 'PAID',
                'paid_at': now,
                'error_code': '',
                'error_description': '',
            }
        )

        if order.order_type == 'RESALE' and resale_listing:
            # Credit the original seller's wallet with resale listing price BEFORE ownership is transferred
            seller = resale_listing.seller
            seller_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=seller)
            if not WalletTransaction.objects.filter(order=order).exists():
                credit_amount = resale_listing.asking_price
                seller_wallet.balance += credit_amount
                seller_wallet.save(update_fields=['balance', 'updated_at'])
                WalletTransaction.objects.create(
                    wallet=seller_wallet,
                    order=order,
                    ticket=ticket,
                    amount=credit_amount,
                    transaction_type='CREDIT',
                    description=f"Resale payout for {ticket.event.title} ({ticket.seat_or_coach})"
                )

            resale_listing.status = 'SOLD'
            resale_listing.reserved_by = None
            resale_listing.reserved_until = None
            resale_listing.save(update_fields=['status', 'reserved_by', 'reserved_until'])
            ticket.invalidate_qr_credential()
        elif not ticket.booking_reference:
            ticket.booking_reference = f"DIR-{uuid.uuid4().hex[:8].upper()}"

        ticket.owner = user
        ticket.attendee_name = order.attendee_name
        ticket.status = 'ACTIVE'
        ticket.reserved_by = None
        ticket.reserved_until = None
        ticket.generate_qr_credential(force=True)
        ticket.save()

        order.status = 'COMPLETED'
        order.save(update_fields=['status'])

    return {
        'success': True,
        'message': 'Payment verified and ticket successfully issued to your vault!',
        'order': {
            'order_number': order.order_number,
            'order_type': order.order_type,
            'status': order.status,
            'total_amount': str(order.total_amount),
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_order_id': order.razorpay_order_id,
        },
        'ticket': {
            'id': ticket.id,
            'ticket_ref': ticket.ticket_ref,
            'booking_reference': ticket.booking_reference,
            'title': ticket.event.title,
            'venue_or_route': ticket.event.venue_or_route,
            'seat_or_coach': ticket.seat_or_coach,
            'attendee_name': ticket.attendee_name,
            'qr_token': ticket.qr_token,
            'qr_image_url': ticket.get_qr_image_base64() if ticket else None,
        }
    }, None, 200


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_razorpay_payment(request, order_id):
    """
    Verifies the cryptographic Razorpay payment signature and completes booking/transfer.
    1. HMAC SHA-256 signature verification.
    2. Server-side fetch from Razorpay API to confirm captured amount.
    3. Row-level locks to prevent double-booking or duplicate transfers.
    4. Issues new ticket or transfers ticket to buyer and generates secure QR credential.
    5. In resale transfers, invalidates the seller's old QR and issues fresh credential to buyer.
    """
    razorpay_order_id = request.data.get('razorpay_order_id', '')
    razorpay_payment_id = request.data.get('razorpay_payment_id', '')
    razorpay_signature = request.data.get('razorpay_signature', '')
    result_data, err, code = _execute_verify_payment(
        request.user, order_id, razorpay_order_id, razorpay_payment_id, razorpay_signature
    )
    if err:
        return Response(err, status=code)
    return Response(result_data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_order_history(request):
    """Returns order history for the authenticated buyer."""
    orders = Order.objects.filter(
        buyer=request.user
    ).select_related('ticket', 'ticket__event', 'ticket__event__provider')

    results = []
    for o in orders:
        t = o.ticket
        ev = t.event if t else None
        results.append({
            'id': o.id,
            'order_number': o.order_number,
            'order_type': o.order_type,
            'ticket_id': t.id if t else None,
            'ticket_ref': t.ticket_ref if t else 'N/A',
            'title': ev.title if ev else 'Ticket Order',
            'category': ev.category if ev else 'N/A',
            'venue_or_route': ev.venue_or_route if ev else '',
            'seat_or_coach': t.seat_or_coach if t else '',
            'ticket_price': str(o.ticket_price),
            'service_fee': str(o.service_fee),
            'total_amount': str(o.total_amount),
            'status': o.status,
            'attendee_name': o.attendee_name,
            'razorpay_order_id': o.razorpay_order_id,
            'created_at': o.created_at.isoformat(),
        })

    return Response({'orders': results})


# ==============================================================================
# Traditional Django Template Web Views
# ==============================================================================

def _is_ajax_or_json(request):
    return (
        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
        request.content_type == 'application/json' or
        'application/json' in request.headers.get('Accept', '')
    )


@login_required
@require_http_methods(['GET', 'POST'])
def checkout_view(request, pk):
    """
    Renders checkout page and initiates 10-minute reservation and Razorpay order.
    Enforces mandatory Setu DigiLocker KYC identity verification.
    """
    listing = _resolve_listing(pk, user=request.user)
    if not listing:
        messages.error(request, 'The requested ticket listing was not found.')
        return redirect('explore')

    venue = listing.get('venue_or_route', '')
    if '→' in venue:
        parts = [p.strip() for p in venue.split('→', 1)]
        listing['origin'] = parts[0]
        listing['destination'] = parts[1]
    elif ' to ' in venue.lower():
        parts = [p.strip() for p in venue.split(' to ', 1)]
        listing['origin'] = parts[0]
        listing['destination'] = parts[1]
    elif ' - ' in venue:
        parts = [p.strip() for p in venue.split(' - ', 1)]
        listing['origin'] = parts[0]
        listing['destination'] = parts[1]
    else:
        listing['origin'] = venue
        listing['destination'] = ''

    is_owner = (
        not listing.get('is_direct') and
        request.user.is_authenticated and
        listing.get('seller_username') and
        request.user.username == listing['seller_username']
    )
    if is_owner:
        if _is_ajax_or_json(request):
            return JsonResponse({'error': 'Sellers are not permitted to purchase their own ticket listings.'}, status=400)
        messages.warning(request, 'Sellers are not permitted to purchase their own ticket listings.')
        return redirect('listing-detail', pk=pk)

    if request.method != 'POST' and not listing.get('is_available'):
        messages.error(request, 'This ticket is currently reserved or unavailable for checkout.')
        return redirect('listing-detail', pk=pk)

    is_kyc_verified = bool(request.user.is_identity_verified and request.user.verified_legal_name)
    default_attendee = request.user.verified_legal_name or request.user.username

    # Mandatory DigiLocker identity verification before any purchase (direct booking or resale)
    if not is_kyc_verified:
        err_msg = 'DigiLocker identity verification is required before purchasing any ticket.'
        if _is_ajax_or_json(request):
            return JsonResponse({'error': err_msg, 'code': 'KYC_REQUIRED'}, status=403)
        messages.warning(request, err_msg)
        action_param = 'resale_purchase' if not listing.get('is_direct') else 'direct_booking'
        return redirect(f"{reverse('verification')}?returnTo={request.path}&action={action_param}")

    if request.method == 'POST':
        attendee_name = ''
        if request.content_type == 'application/json' and request.body:
            try:
                data = json.loads(request.body.decode('utf-8'))
                attendee_name = data.get('attendee_name', '')
            except Exception:
                pass
        if not attendee_name:
            attendee_name = request.POST.get('attendee_name', '')

        order_dict, rzp_dict, err, code = _execute_checkout_order(
            request.user, pk, attendee_name or default_attendee
        )

        if err:
            if _is_ajax_or_json(request):
                return JsonResponse(err, status=code)
            messages.error(request, err.get('error', 'Failed to initiate checkout.'))
            return redirect('checkout', pk=pk)

        if _is_ajax_or_json(request):
            return JsonResponse({
                'success': True,
                'order': order_dict,
                'razorpay': rzp_dict,
            }, status=201)

        return render(request, 'orders/checkout.html', {
            'listing': listing,
            'is_kyc_verified': True,
            'attendee_name': attendee_name or default_attendee,
            'razorpay_key_id': settings.RAZORPAY_KEY_ID,
            'order': order_dict,
            'razorpay': rzp_dict,
        })

    return render(request, 'orders/checkout.html', {
        'listing': listing,
        'is_kyc_verified': is_kyc_verified,
        'attendee_name': default_attendee,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
    })


def _extract_payment_callback_data(request):
    """Extracts Razorpay payment tokens or failure parameters from JSON body, POST form, or GET query."""
    data = {}
    if request.content_type == 'application/json' and request.body:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}

    def _get_val(k1, k2=None):
        return (data.get(k1) or request.POST.get(k1) or request.GET.get(k1) or
                (request.POST.get(k2) if k2 else None) or
                (request.GET.get(k2) if k2 else None) or '')

    return {
        'error_code': _get_val('error[code]', 'error_code'),
        'error_desc': _get_val('error[description]', 'error_description'),
        'error_reason': _get_val('error[reason]', 'error_reason'),
        'razorpay_order_id': _get_val('razorpay_order_id').strip(),
        'razorpay_payment_id': _get_val('razorpay_payment_id').strip(),
        'razorpay_signature': _get_val('razorpay_signature').strip(),
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def verify_payment_web_view(request, order_id):
    """
    Cryptographically verifies Razorpay payment and completes booking/issuance.
    Supports:
    1. Direct browser redirect callback from Razorpay (POST with form data or error)
    2. Client-side modal handler verification (POST with JSON)
    3. Safe GET fallback handling
    """
    order = Order.objects.select_related('buyer', 'ticket', 'ticket__event', 'resale_listing').filter(pk=order_id).first()
    if not order:
        if _is_ajax_or_json(request):
            return JsonResponse({'error': 'Order not found.'}, status=404)
        messages.error(request, 'The requested order was not found.')
        return redirect('user-orders' if request.user.is_authenticated else 'explore')

    listing_item_id = order.ticket_id if order.order_type == 'DIRECT' else (order.resale_listing_id or order.ticket_id)
    listing_key = f"{'dir' if order.order_type == 'DIRECT' else 'res'}-{listing_item_id}"

    # Determine acting buyer user and enforce authorization
    if request.user.is_authenticated:
        if request.user != order.buyer and not request.user.is_staff:
            if _is_ajax_or_json(request):
                return JsonResponse({'error': 'Unauthorized to access this order.'}, status=403)
            messages.error(request, 'Unauthorized access to this order.')
            return redirect('explore')
        buyer_user = request.user
    else:
        # Cross-site POST callback from payment gateway where session cookie was withheld by browser SameSite policy
        is_callback_post = request.method == 'POST' and (
            request.POST.get('razorpay_payment_id') or
            (request.content_type == 'application/json' and request.body)
        )
        if is_callback_post:
            buyer_user = order.buyer
        else:
            if _is_ajax_or_json(request):
                return JsonResponse({'error': 'Authentication required.'}, status=401)
            messages.error(request, 'Please log in to view this order.')
            return redirect('login')

    # Idempotent response if already completed
    if order.status == 'COMPLETED':
        ticket_id = order.ticket_id
        redirect_url = reverse('ticket-detail-view', args=[ticket_id])
        if _is_ajax_or_json(request):
            return JsonResponse({'success': True, 'redirect_url': redirect_url})
        messages.info(request, 'This order has already been successfully confirmed.')
        return redirect(redirect_url)

    if order.status == 'CANCELLED':
        existing_paid = Payment.objects.filter(order=order, status='PAID').first()
        if existing_paid:
            if _is_ajax_or_json(request):
                return JsonResponse({'error': 'This order was cancelled due to reservation expiry.', 'code': 'ORDER_CANCELLED'}, status=409)
            messages.error(request, 'This order was cancelled because the ticket reservation expired.')
            return redirect('user-orders' if request.user.is_authenticated else 'explore')

    if order.status == 'REFUNDED':
        if _is_ajax_or_json(request):
            return JsonResponse({'error': 'This order has already been refunded.', 'code': 'ORDER_REFUNDED'}, status=409)
        messages.info(request, 'This order has already been refunded.')
        return redirect('user-orders' if request.user.is_authenticated else 'explore')

    callback_data = _extract_payment_callback_data(request)
    error_code = callback_data['error_code']
    error_desc = callback_data['error_desc']
    error_reason = callback_data['error_reason']

    if error_code or error_desc or error_reason:
        fail_msg = error_desc or error_reason or 'Payment was declined or cancelled at the payment gateway.'
        if order.status == 'PENDING':
            order.status = 'PAYMENT_FAILED'
            order.save(update_fields=['status'])
        if _is_ajax_or_json(request):
            return JsonResponse({'error': fail_msg, 'code': error_code or 'PAYMENT_FAILED'}, status=400)
        messages.error(request, f"Payment failed: {fail_msg}")
        return redirect('checkout', pk=listing_key)

    razorpay_order_id = callback_data['razorpay_order_id']
    razorpay_payment_id = callback_data['razorpay_payment_id']
    razorpay_signature = callback_data['razorpay_signature']

    # If GET with no payment tokens, redirect safely back to checkout
    if request.method == 'GET' and not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        messages.warning(request, 'Payment was not completed. You may retry your payment below.')
        return redirect('checkout', pk=listing_key)

    result_data, err, code = _execute_verify_payment(
        buyer_user,
        order_id,
        razorpay_order_id,
        razorpay_payment_id,
        razorpay_signature,
    )

    if err:
        if _is_ajax_or_json(request):
            return JsonResponse(err, status=code)
        messages.error(request, err.get('error', 'Payment verification failed.'))
        if err.get('code') in ('RESERVATION_EXPIRED', 'ORDER_CANCELLED', 'ORDER_REFUNDED'):
            return redirect('user-orders' if buyer_user.is_authenticated else 'explore')
        return redirect('checkout', pk=listing_key)

    # Ensure user session is restored after third-party bank redirect
    if not request.user.is_authenticated:
        auth_login(request, buyer_user, backend='django.contrib.auth.backends.ModelBackend')

    ticket_id = result_data['ticket']['id']
    redirect_url = reverse('ticket-detail-view', args=[ticket_id])

    messages.success(request, 'Payment confirmed! Your ticket has been issued to your Ticket Vault.')

    if _is_ajax_or_json(request):
        return JsonResponse({
            'success': True,
            'redirect_url': redirect_url,
            'result': result_data,
        })

    return redirect(redirect_url)


@login_required
@require_http_methods(['GET'])
def order_history_view(request):
    """
    Order history view for the authenticated buyer.
    Strictly isolated: queries buyer=request.user only.
    """
    orders = Order.objects.filter(
        buyer=request.user
    ).select_related('ticket', 'ticket__event', 'ticket__event__provider')
    return render(request, 'orders/order_history.html', {'orders': orders})
