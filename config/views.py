from django.conf import settings
from django.db import connection
from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint to verify backend status, database connectivity,
    and external integration readiness.
    """
    db_ok = True
    try:
        connection.ensure_connection()
    except Exception:
        db_ok = False

    # Check Setu DigiLocker configuration status honestly
    setu_configured = bool(
        settings.SETU_CLIENT_ID and
        settings.SETU_CLIENT_SECRET and
        settings.SETU_PRODUCT_INSTANCE_ID
    )

    # Check Razorpay configuration status honestly
    razorpay_configured = bool(
        settings.RAZORPAY_KEY_ID and 
        settings.RAZORPAY_KEY_SECRET
    )

    return Response({
        'status': 'healthy' if db_ok else 'degraded',
        'service': 'P2P Ticket Reselling Platform API',
        'database': 'connected' if db_ok else 'disconnected',
        'setu_digilocker': {
            'configured': setu_configured,
            'base_url': settings.SETU_BASE_URL,
            'status_detail': 'Ready' if setu_configured else 'Credentials missing in environment (SETU_CLIENT_ID, SETU_CLIENT_SECRET, SETU_PRODUCT_INSTANCE_ID)'
        },
        'razorpay': {
            'configured': razorpay_configured,
            'status_detail': 'Ready' if razorpay_configured else 'Credentials missing in environment (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)'
        }
    })


def custom_404_view(request, exception=None):
    """
    Renders custom 404 Ticket Not Found severed-pass template
    with diagnostic ledger and escrow safe indicators.
    """
    setattr(request, '_custom_404_rendered', True)
    return render(request, '404.html', {
        'error_title': 'Ticket Not Found',
        'error_message': 'The requested voucher was severed at the perforation line. The tokenized pass is voided, redeemed, or unresolvable.',
        'requested_path': getattr(request, 'path', ''),
    }, status=404)


