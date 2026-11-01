from decimal import Decimal
from django.contrib.auth import authenticate, login, logout
from django.db import models
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import User, SetuKYCRecord, Wallet, WalletTransaction
from .setu_client import SetuClient
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods


@api_view(['POST'])
@permission_classes([AllowAny])
def register_view(request):
    username = request.data.get('username', '').strip()
    email = request.data.get('email', '').strip()
    password = request.data.get('password', '')
    phone = request.data.get('phone', '').strip()

    if not username or not email or not password:
        return Response(
            {'error': 'Username, email, and password are required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if User.objects.filter(username__iexact=username).exists():
        return Response({'error': 'Username is already taken.'}, status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(email__iexact=email).exists():
        return Response({'error': 'Email is already registered.'}, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        phone=phone
    )

    login(request, user)
    return Response({
        'message': 'Account registered successfully.',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_identity_verified': user.is_identity_verified,
            'verified_legal_name': user.verified_legal_name
        }
    }, status=status.HTTP_201_CREATED)


def _authenticate_by_username_or_email(request, username_or_email, password):
    """Authenticates user either by username directly or by email address."""
    user = authenticate(request, username=username_or_email, password=password)
    if user is None:
        user_obj = User.objects.filter(email__iexact=username_or_email).first()
        if user_obj:
            user = authenticate(request, username=user_obj.username, password=password)
    return user


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')

    if not username or not password:
        return Response({'error': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    user = _authenticate_by_username_or_email(request, username, password)

    if user is None:
        return Response({'error': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

    login(request, user)
    return Response({
        'message': 'Logged in successfully.',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_identity_verified': user.is_identity_verified,
            'verified_legal_name': user.verified_legal_name
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response({'message': 'Logged out successfully.'})


def sync_user_kyc_status(user, record=None):
    if user.is_identity_verified and user.verified_legal_name:
        return {
            'status': 'VERIFIED',
            'verified_legal_name': user.verified_legal_name,
            'message': 'Identity is already verified.'
        }

    latest_kyc = record or user.kyc_records.order_by('-created_at').first()
    if not latest_kyc:
        return {'status': 'NOT_STARTED', 'detail': 'No verification attempt found.'}

    if latest_kyc.status == 'VERIFIED' and latest_kyc.verified_legal_name:
        if not user.is_identity_verified or user.verified_legal_name != latest_kyc.verified_legal_name:
            user.is_identity_verified = True
            user.verified_legal_name = latest_kyc.verified_legal_name
            user.save(update_fields=['is_identity_verified', 'verified_legal_name'])
        return {
            'status': 'VERIFIED',
            'verified_legal_name': user.verified_legal_name,
            'verified_at': latest_kyc.verified_at.isoformat() if latest_kyc.verified_at else None
        }

    if not SetuClient.is_configured():
        return {
            'status': 'UNCONFIGURED',
            'detail': 'Setu credentials not configured in environment.'
        }

    status_resp = SetuClient.get_request_status(latest_kyc.setu_request_id)
    if not status_resp.get('success'):
        return {
            'status': latest_kyc.status,
            'error': status_resp.get('error')
        }

    remote_status = status_resp.get('status')
    if remote_status == 'AUTHENTICATED':
        doc_resp = SetuClient.fetch_aadhaar_data(latest_kyc.setu_request_id)
        if doc_resp.get('success'):
            legal_name = doc_resp.get('verified_legal_name')
            if legal_name:
                latest_kyc.status = 'VERIFIED'
                latest_kyc.verified_legal_name = legal_name
                latest_kyc.verified_at = timezone.now()
                latest_kyc.save(update_fields=['status', 'verified_legal_name', 'verified_at'])

                user.is_identity_verified = True
                user.verified_legal_name = legal_name
                user.save(update_fields=['is_identity_verified', 'verified_legal_name'])

                return {
                    'status': 'VERIFIED',
                    'verified_legal_name': legal_name,
                    'message': 'Setu DigiLocker identity verification successfully completed.'
                }

        latest_kyc.status = 'FAILED'
        latest_kyc.error_message = doc_resp.get('error', 'Could not retrieve legal name from Setu response.')
        latest_kyc.save(update_fields=['status', 'error_message'])
        return {
            'status': 'FAILED',
            'error': latest_kyc.error_message
        }

    if remote_status == 'EXPIRED':
        if latest_kyc.status != 'EXPIRED':
            latest_kyc.status = 'EXPIRED'
            latest_kyc.save(update_fields=['status'])
        return {'status': 'EXPIRED', 'detail': 'DigiLocker consent session expired.'}

    if remote_status in ('FAILED', 'REJECTED'):
        if latest_kyc.status != 'FAILED':
            latest_kyc.status = 'FAILED'
            latest_kyc.error_message = status_resp.get('error', 'DigiLocker consent was rejected or failed.')
            latest_kyc.save(update_fields=['status', 'error_message'])
        return {'status': 'FAILED', 'error': latest_kyc.error_message}

    return {
        'status': 'INITIATED',
        'detail': 'Waiting for user to complete DigiLocker authentication.'
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def me_view(request):
    if not request.user.is_authenticated:
        return Response({'authenticated': False, 'user': None})

    user = request.user

    latest_kyc = user.kyc_records.order_by('-created_at').first()
    if not user.is_identity_verified and latest_kyc and latest_kyc.status in ('INITIATED', 'VERIFIED'):
        try:
            sync_user_kyc_status(user)
            user.refresh_from_db()
            latest_kyc.refresh_from_db()
        except Exception:
            pass

    kyc_info = None
    if latest_kyc:
        kyc_info = {
            'status': latest_kyc.status,
            'request_id': latest_kyc.setu_request_id,
            'verified_legal_name': latest_kyc.verified_legal_name,
            'created_at': latest_kyc.created_at.isoformat(),
            'verified_at': latest_kyc.verified_at.isoformat() if latest_kyc.verified_at else None,
            'error_message': latest_kyc.error_message
        }

    return Response({
        'authenticated': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'phone': user.phone,
            'is_identity_verified': user.is_identity_verified,
            'verified_legal_name': user.verified_legal_name,
            'kyc': kyc_info
        },
        'setu_status': {
            'is_configured': SetuClient.is_configured(),
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def initiate_setu_kyc(request):
    user = request.user
    if user.is_identity_verified:
        return Response({
            'message': 'Identity is already verified.',
            'status': 'VERIFIED',
            'verified_legal_name': user.verified_legal_name
        })

    if not SetuClient.is_configured():
        return Response({
            'error': 'Setu DigiLocker sandbox credentials are not configured in backend environment.',
            'status': 'UNCONFIGURED',
            'instruction': 'Add SETU_CLIENT_ID, SETU_CLIENT_SECRET, and SETU_PRODUCT_INSTANCE_ID to backend/.env'
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    redirect_url = request.data.get('redirect_url')
    result = SetuClient.create_digilocker_request(redirect_url=redirect_url)

    if not result.get('success'):
        return Response({
            'error': result.get('error'),
            'status': result.get('status')
        }, status=status.HTTP_502_BAD_GATEWAY)

    record = SetuKYCRecord.objects.create(
        user=user,
        setu_request_id=result['request_id'],
        status='INITIATED',
        digilocker_url=result.get('url', ''),
    )

    return Response({
        'status': 'INITIATED',
        'setu_request_id': record.setu_request_id,
        'digilocker_url': record.digilocker_url,
        'valid_upto': result.get('valid_upto'),
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_setu_kyc_status(request):
    user = request.user
    result = sync_user_kyc_status(user)
    if result.get('status') == 'FAILED':
        return Response(result, status=status.HTTP_502_BAD_GATEWAY)
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_wallet_summary(request):
    """Returns wallet balance, total resale earnings, and credit history for authenticated user."""
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    transactions = (
        WalletTransaction.objects.filter(wallet=wallet)
        .select_related('order', 'ticket', 'ticket__event', 'order__ticket', 'order__ticket__event')
        .order_by('-created_at')
    )
    total_earnings = (
        transactions.filter(transaction_type='CREDIT')
        .aggregate(total=models.Sum('amount'))['total']
        or Decimal('0.00')
    )

    results = []
    for tx in transactions:
        t = tx.ticket or (tx.order.ticket if tx.order else None)
        ev = t.event if t else None
        results.append({
            'id': tx.id,
            'amount': str(tx.amount),
            'transaction_type': tx.transaction_type,
            'description': tx.description,
            'order_number': tx.order.order_number if tx.order else None,
            'ticket_ref': t.ticket_ref if t else '',
            'seat_or_coach': t.seat_or_coach if t else '',
            'event_title': ev.title if ev else '',
            'created_at': tx.created_at.isoformat(),
        })

    return Response({
        'balance': f"{wallet.balance:.2f}",
        'currency': 'INR',
        'total_resale_earnings': f"{Decimal(str(total_earnings)):.2f}",
        'transactions': results,
    })


# ==============================================================================
# Traditional Django Template Web Views
# ==============================================================================

@require_http_methods(['GET', 'POST'])
def user_login(request):
    if request.user.is_authenticated:
        return redirect('explore')

    next_url = request.GET.get('next') or request.POST.get('next') or 'explore'
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = 'explore'

    if request.method == 'POST':
        username_or_email = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if not username_or_email or not password:
            messages.error(request, 'Please provide both username/email and password.')
            return render(request, 'accounts/login.html', {'username': username_or_email, 'next': next_url})

        user = _authenticate_by_username_or_email(request, username_or_email, password)

        if user is None:
            messages.error(request, 'Invalid credentials. Please verify your username/email and password.')
            return render(request, 'accounts/login.html', {'username': username_or_email, 'next': next_url})

        login(request, user)
        messages.success(request, f'Welcome back, {user.username}!')
        return redirect(next_url)

    return render(request, 'accounts/login.html', {'next': next_url})


@require_http_methods(['GET', 'POST'])
def user_register(request):
    if request.user.is_authenticated:
        return redirect('explore')

    next_url = request.GET.get('next') or request.POST.get('next') or 'explore'
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = 'explore'

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '') or request.POST.get('confirm-password', '')
        phone = request.POST.get('phone', '').strip() or request.POST.get('mobile', '').strip()

        context = {
            'username': username,
            'email': email,
            'phone': phone,
            'next': next_url,
        }

        if not username or not email or not password:
            messages.error(request, 'Username, email, and password are required.')
            return render(request, 'accounts/register.html', context)

        if password != password_confirm:
            messages.error(request, 'Passwords do not match. Please re-enter your password.')
            return render(request, 'accounts/register.html', context)

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, 'This username is already taken. Please choose another.')
            return render(request, 'accounts/register.html', context)

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, 'An account with this email is already registered.')
            return render(request, 'accounts/register.html', context)

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            phone=phone,
        )

        login(request, user)
        messages.success(request, 'Account created successfully! Welcome to lastchance.')
        return redirect(next_url)

    return render(request, 'accounts/register.html', {'next': next_url})


@require_http_methods(['POST'])
def user_logout(request):
    if request.user.is_authenticated:
        logout(request)
        messages.info(request, 'You have been logged out successfully.')
    return redirect('login')


@login_required
@require_http_methods(['GET', 'POST'])
def verification_view(request):
    """
    Renders DigiLocker KYC verification status page and initiates Setu consent requests.
    Prevents unauthorized or client-side tampering with verification status.
    """
    user = request.user
    return_to = request.GET.get('returnTo') or request.POST.get('returnTo') or ''
    if return_to and not url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}):
        return_to = ''

    action_param = request.GET.get('action') or request.GET.get('reason') or request.POST.get('action') or ''

    is_verified = bool(user.is_identity_verified and user.verified_legal_name)
    latest_kyc = user.kyc_records.order_by('-created_at').first()

    if request.method == 'POST':
        # Users cannot self-verify via POST; verification can ONLY occur through authoritative Setu exchange
        if is_verified:
            messages.info(request, 'Your identity is already verified.')
            if return_to:
                return redirect(return_to)
            return redirect('verification')

        if not SetuClient.is_configured():
            messages.error(request, 'Setu DigiLocker gateway credentials are not configured in the environment.')
            return redirect('verification')

        if return_to:
            request.session['kyc_return_to'] = return_to

        callback_url = request.build_absolute_uri(reverse('kyc-callback'))
        result = SetuClient.create_digilocker_request(redirect_url=callback_url)

        if not result.get('success'):
            error_message = result.get('error') or 'Failed to initiate Setu DigiLocker verification session.'
            messages.error(request, f"Setu DigiLocker Error: {error_message}")
            return redirect('verification')

        record = SetuKYCRecord.objects.create(
            user=user,
            setu_request_id=result['request_id'],
            status='INITIATED',
            digilocker_url=result.get('url', ''),
        )
        request.session['active_kyc_request_id'] = record.setu_request_id

        if record.digilocker_url:
            return redirect(record.digilocker_url)

        messages.info(request, 'DigiLocker consent session created. Please proceed with authentication.')
        return redirect('verification')

    return render(request, 'accounts/verification.html', {
        'is_verified': is_verified,
        'verified_legal_name': user.verified_legal_name,
        'is_setu_configured': SetuClient.is_configured(),
        'return_to': return_to,
        'action_param': action_param,
        'latest_kyc': latest_kyc,
    })


@login_required
@require_http_methods(['GET'])
def kyc_callback_view(request):
    """
    Handles return redirect from Setu DigiLocker.
    Security:
    - Never marks user verified merely because of hitting this callback.
    - Requires matching active Setu request for the authenticated user.
    - Authoritatively queries Setu server-to-server API to verify status and retrieve verified name.
    """
    user = request.user
    request_id = request.GET.get('id') or request.session.get('active_kyc_request_id')

    return_to = request.session.get('kyc_return_to') or request.GET.get('returnTo') or ''
    if return_to and not url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}):
        return_to = ''

    if not request_id:
        messages.error(request, 'Missing verification session identifier.')
        return render(request, 'accounts/kyc_callback.html', {
            'status': 'FAILED',
            'error': 'No active verification session was found.',
            'return_to': return_to,
        })

    record = SetuKYCRecord.objects.filter(user=user, setu_request_id=request_id).first()
    if not record:
        messages.error(request, 'Invalid or unrecognized verification session.')
        return render(request, 'accounts/kyc_callback.html', {
            'status': 'FAILED',
            'error': 'Verification session does not exist or belongs to another user.',
            'return_to': return_to,
        })

    sync_result = sync_user_kyc_status(user, record=record)
    user.refresh_from_db()
    record.refresh_from_db()

    is_verified = bool(user.is_identity_verified and user.verified_legal_name)

    if is_verified:
        request.session.pop('active_kyc_request_id', None)
        messages.success(request, f'Identity verified successfully as {user.verified_legal_name}!')
        return render(request, 'accounts/kyc_callback.html', {
            'status': 'VERIFIED',
            'verified_legal_name': user.verified_legal_name,
            'request_id': record.setu_request_id,
            'return_to': return_to,
        })

    status_val = sync_result.get('status') or record.status
    error_detail = sync_result.get('error') or sync_result.get('detail') or record.error_message or 'DigiLocker consent was not completed.'

    messages.error(request, f'Verification {status_val.lower()}: {error_detail}')
    return render(request, 'accounts/kyc_callback.html', {
        'status': status_val,
        'error': error_detail,
        'request_id': record.setu_request_id,
        'return_to': return_to,
    })


@login_required
@require_http_methods(['GET'])
def my_wallet_view(request):
    """
    Renders the authenticated user's wallet overview and resale transaction history.
    Users can only view their own wallet and transaction history.
    """
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    transactions = (
        WalletTransaction.objects.filter(wallet=wallet)
        .select_related('order', 'ticket', 'ticket__event', 'order__ticket', 'order__ticket__event')
        .order_by('-created_at')
    )
    total_earnings = (
        transactions.filter(transaction_type='CREDIT')
        .aggregate(total=models.Sum('amount'))['total']
        or Decimal('0.00')
    )

    CATEGORY_MAPPING = {
        'train': ('trains', 'train_count'),
        'flight': ('flights', 'flight_count'),
        'concert': ('concerts', 'concert_count'),
        'festival': ('concerts', 'concert_count'),
        'live': ('concerts', 'concert_count'),
        'sports': ('sports', 'sports_count'),
        'movie': ('movies', 'movie_count'),
        'cinema': ('movies', 'movie_count'),
    }
    counts = {
        'train_count': 0,
        'flight_count': 0,
        'concert_count': 0,
        'sports_count': 0,
        'movie_count': 0,
    }

    tx_list = list(transactions)
    for tx in tx_list:
        t = tx.ticket or (tx.order.ticket if tx.order else None)
        ev = t.event if t else None
        cat = (t.category if t and hasattr(t, 'category') and t.category else (ev.category if ev and hasattr(ev, 'category') else 'other')).lower()

        filter_cat, count_key = CATEGORY_MAPPING.get(cat, ('all', None))
        if count_key:
            counts[count_key] += 1

        title = ev.title if ev else (t.title if t and hasattr(t, 'title') else (tx.description or 'Resale Ticket Payout'))
        venue = ev.venue_or_route if ev else (t.venue_or_route if t and hasattr(t, 'venue_or_route') else '')
        seat = t.seat_or_coach if t and hasattr(t, 'seat_or_coach') else 'General Admission'
        ref = (t.ticket_ref if t and hasattr(t, 'ticket_ref') else '') or (tx.order.order_number if tx.order else f'TX-{tx.id}')
        pnr = (t.booking_reference if t and hasattr(t, 'booking_reference') and t.booking_reference else '') or ref
        face_val = str(t.face_value) if t and hasattr(t, 'face_value') and t.face_value else str(tx.amount)

        tx.event_title = title
        tx.venue_or_route = venue
        tx.seat_or_coach = seat
        tx.ticket_ref = ref
        tx.booking_reference = pnr
        tx.face_value = face_val
        tx.event_category = cat
        tx.filter_cat = filter_cat
        tx.formatted_date = tx.created_at.strftime('%b %d, %Y • %I:%M %p')
        tx.search_text = f"{title} {venue} {ref} {pnr} {seat} {cat} {tx.amount}".lower()

    context = {
        'wallet': wallet,
        'balance': wallet.balance,
        'total_earnings': total_earnings,
        'transactions': tx_list,
        **counts,
    }
    return render(request, 'accounts/wallet.html', context)


