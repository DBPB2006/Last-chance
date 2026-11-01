from django.urls import path
from .views import create_checkout_order, verify_razorpay_payment, get_order_history

urlpatterns = [
    path('checkout/', create_checkout_order, name='order-checkout'),
    path('<int:order_id>/verify-payment/', verify_razorpay_payment, name='order-verify-payment'),
    path('history/', get_order_history, name='order-history'),
]
