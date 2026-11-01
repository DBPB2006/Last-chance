from decimal import Decimal
from django.db import models
from django.conf import settings


class Order(models.Model):
    ORDER_TYPE_CHOICES = [
        ('DIRECT', 'Direct Booking'),
        ('RESALE', 'Peer-to-Peer Resale'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Payment Pending'),
        ('PAID', 'Payment Captured / Transfer Pending'),
        ('PAYMENT_FAILED', 'Payment Declined / Failed'),
        ('COMPLETED', 'Confirmed & Ticket Issued'),
        ('REFUNDED', 'Payment Refunded'),
        ('CANCELLED', 'Cancelled / Reservation Expired'),
    ]

    order_number = models.CharField(max_length=64, unique=True)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='DIRECT')

    # Points to the authoritative Ticket
    ticket = models.ForeignKey('ticketing.Ticket', on_delete=models.CASCADE, related_name='orders', null=True, blank=True)
    # If peer-to-peer resale, points to ResaleListing
    resale_listing = models.ForeignKey('ticketing.ResaleListing', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')

    ticket_price = models.DecimalField(max_digits=10, decimal_places=2)
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING')
    attendee_name = models.CharField(max_length=255)
    razorpay_order_id = models.CharField(max_length=100, blank=True, default='', db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.order_number} ({self.order_type}) - ₹{self.total_amount}"


class Payment(models.Model):
    STATUS_CHOICES = [
        ('CREATED', 'Created'),
        ('PAID', 'Payment Captured'),
        ('FAILED', 'Payment Failed'),
        ('REFUNDED', 'Payment Refunded'),
    ]

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment')
    razorpay_order_id = models.CharField(max_length=100, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, default='')

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='INR')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='CREATED')

    error_code = models.CharField(max_length=100, blank=True, default='')
    error_description = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment {self.razorpay_payment_id or self.razorpay_order_id} - ₹{self.amount} ({self.status})"
