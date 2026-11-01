import base64
import hashlib
import io
import os
import secrets
import qrcode
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone


def build_ticket_url(credential, request=None, base_url=None):
    """
    Constructs the absolute URL for the QR credential.
    Uses TICKET_BASE_URL configured through settings and environment variables.
    Strips trailing slashes to guarantee clean URL construction.
    """
    from django.urls import reverse
    path = reverse('ticket-qr-validate', args=[credential])

    # Explicit base_url takes precedence if supplied
    if base_url:
        return f"{base_url.strip().rstrip('/')}{path}"

    # Read from configured Django settings or environment
    configured_base = getattr(settings, 'TICKET_BASE_URL', None) or os.getenv('TICKET_BASE_URL', '')
    if configured_base and str(configured_base).strip():
        return f"{str(configured_base).strip().rstrip('/')}{path}"

    if request is not None:
        return request.build_absolute_uri(path)

    default_base = 'http://127.0.0.1:8000'
    return f"{default_base}{path}"


class Provider(models.Model):
    CATEGORY_CHOICES = [
        ('flight', 'Flight'),
        ('train', 'Train'),
        ('movie', 'Movie'),
        ('concert', 'Concert'),
        ('festival', 'Festival'),
        ('sports', 'Sports'),
        ('other', 'Other Events'),
    ]

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, db_index=True)
    description = models.TextField(blank=True, default='')
    logo_url = models.URLField(max_length=500, blank=True, default='')
    website = models.URLField(max_length=500, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class Event(models.Model):
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name='events')
    category = models.CharField(max_length=30, choices=Provider.CATEGORY_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    venue_or_route = models.CharField(max_length=255)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    itinerary_or_details = models.TextField(blank=True, default='')
    image_url = models.URLField(max_length=500, blank=True, default='')
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_time']

    def __str__(self):
        return f"{self.title} - {self.venue_or_route}"


class Ticket(models.Model):
    STATUS_CHOICES = [
        ('AVAILABLE', 'Available for Direct Booking'),
        ('RESERVED', 'Reserved in Checkout'),
        ('ACTIVE', 'Active & Valid in Vault'),
        ('LISTED', 'Listed for Peer-to-Peer Resale'),
        ('USED', 'Used / Admitted at Gate'),
        ('CANCELLED', 'Cancelled'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='tickets')
    ticket_ref = models.CharField(max_length=64, unique=True, db_index=True)
    seat_or_coach = models.CharField(max_length=100)
    face_value = models.DecimalField(max_digits=10, decimal_places=2)

    # Ownership:
    # owner is None -> pre-created direct inventory from provider
    # owner is set -> user-owned ticket in their vault
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_tickets'
    )
    attendee_name = models.CharField(max_length=255, blank=True, default='')
    booking_reference = models.CharField(max_length=64, blank=True, default='', db_index=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE', db_index=True)

    # Temporary reservation during checkout
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reserved_tickets'
    )
    reserved_until = models.DateTimeField(null=True, blank=True)

    # Cryptographic QR credential stored in database
    qr_token = models.CharField(max_length=128, unique=True, null=True, blank=True)
    qr_token_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)

    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.ticket_ref} - {self.event.title} ({self.seat_or_coach})"

    def generate_qr_credential(self, force=False, request=None, base_url=None):
        """
        Generates or rotates unique QR credential token and SHA-256 hash in the database.
        Does NOT write any image files to disk.
        """
        if not self.qr_token_hash or not self.qr_token or force:
            credential = secrets.token_urlsafe(32)
            self.qr_token = credential
            self.qr_token_hash = hashlib.sha256(credential.encode('utf-8')).hexdigest()

            ticket_url = build_ticket_url(credential, request=request, base_url=base_url)
            self._raw_credential = credential
            self._ticket_url = ticket_url
            return ticket_url
        return build_ticket_url(self.qr_token, request=request, base_url=base_url)

    def invalidate_qr_credential(self):
        """Invalidates current QR credential so prior holder cannot use it."""
        self.qr_token = None
        self.qr_token_hash = ''

    def get_qr_image_pil(self, request=None, base_url=None):
        """
        Dynamically generates a PIL Image of the QR code in memory using BytesIO.
        No files are created or read from disk.
        """
        if not self.qr_token_hash:
            return None
        if not self.qr_token:
            self.generate_qr_credential(force=True, request=request, base_url=base_url)

        ticket_url = build_ticket_url(self.qr_token, request=request, base_url=base_url)
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(ticket_url)
        qr.make(fit=True)
        return qr.make_image(fill_color="#0a0d14", back_color="#ffffff").convert('RGB')

    def get_qr_image_base64(self, request=None, base_url=None):
        """
        Dynamically generates in-memory PNG QR code and returns as Base64 data URI:
        'data:image/png;base64,...'
        Compatible with <img> src, templates, and frontend API responses.
        """
        img = self.get_qr_image_pil(request=request, base_url=base_url)
        if not img:
            return None
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        encoded = base64.b64encode(buf.getvalue()).decode('ascii')
        return f"data:image/png;base64,{encoded}"

    @property
    def qr_image_url(self):
        """Dynamic Base64 data URI for frontend and template compatibility."""
        return self.get_qr_image_base64()

    @property
    def qr_image(self):
        """Dynamic Base64 data URI for backward compatibility."""
        return self.get_qr_image_base64()



class ResaleListing(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active in Marketplace'),
        ('RESERVED', 'Reserved in Checkout'),
        ('SOLD', 'Sold & Transferred'),
        ('WITHDRAWN', 'Withdrawn by Seller'),
    ]

    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name='resale_listing')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='resale_listings')
    asking_price = models.DecimalField(max_digits=10, decimal_places=2)
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('50.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE', db_index=True)

    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reserved_resales'
    )
    reserved_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Resale #{self.id}: {self.ticket.ticket_ref} - ₹{self.asking_price}"

    @property
    def total_buyer_price(self):
        return self.asking_price + self.service_fee

    @property
    def is_currently_available(self):
        if self.status == 'ACTIVE':
            return True
        if self.status == 'RESERVED' and self.reserved_until and timezone.now() > self.reserved_until:
            return True
        return False
