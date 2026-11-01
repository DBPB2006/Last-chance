from django.contrib import admin
from .models import Order, Payment


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'buyer', 'total_amount', 'status', 'attendee_name', 'razorpay_order_id', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('order_number', 'buyer__username', 'attendee_name', 'razorpay_order_id')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('razorpay_order_id', 'razorpay_payment_id', 'order', 'amount', 'currency', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('razorpay_order_id', 'razorpay_payment_id', 'order__order_number')
