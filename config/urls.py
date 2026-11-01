from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from .views import health_check, custom_404_view
from accounts.views import (
    user_login,
    user_register,
    user_logout,
    verification_view,
    kyc_callback_view,
    my_wallet_view,
)

from catalog.views import (
    explore_view,
    listing_detail_view,
    sell_ticket_view,
    my_listings_view,
    withdraw_listing_view,
)
from orders.views import order_history_view, checkout_view, verify_payment_web_view
from ticketing.views import (
    ticket_vault_view,
    ticket_detail_view,
    download_ticket_pdf_view,
    ticket_qr_validate_view,
)


urlpatterns = [
    # Traditional DTL web page routes
    path('', explore_view, name='explore'),
    path('events/<int:pk>/', listing_detail_view, name='event-detail'),
    path('listings/<str:pk>/', listing_detail_view, name='listing-detail'),
    path('checkout/<str:pk>/', checkout_view, name='checkout'),
    path('checkout/<int:order_id>/verify/', verify_payment_web_view, name='checkout-verify'),
    path('vault/', ticket_vault_view, name='ticket-vault'),
    path('vault/<int:pk>/', ticket_detail_view, name='ticket-detail-view'),
    path('vault/<int:pk>/pdf/', download_ticket_pdf_view, name='ticket-download-pdf'),
    path('ticket/<str:credential>/', ticket_qr_validate_view, name='ticket-qr-validate'),
    path('sell/', sell_ticket_view, name='sell-ticket'),
    path('my-listings/', my_listings_view, name='my-listings'),
    path('my-listings/<int:pk>/withdraw/', withdraw_listing_view, name='withdraw-listing'),
    path('orders/', order_history_view, name='user-orders'),
    path('wallet/', my_wallet_view, name='my-wallet'),
    path('login/', user_login, name='login'),
    path('register/', user_register, name='register'),
    path('logout/', user_logout, name='logout'),
    path('verification/', verification_view, name='verification'),
    path('kyc/callback/', kyc_callback_view, name='kyc-callback'),
    path('404/', custom_404_view, name='custom-404'),

    # Administrative
    path('admin/', admin.site.urls),

    # Preserved DRF API routes (active during migration)
    path('api/health/', health_check, name='health-check'),
    path('api/auth/', include('accounts.urls')),
    path('api/catalog/', include('catalog.urls')),
    path('api/orders/', include('orders.urls')),
    path('api/ticketing/', include('ticketing.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += staticfiles_urlpatterns()

handler404 = 'config.views.custom_404_view'


