from django.contrib import admin
from .models import Provider, Event, Ticket, ResaleListing


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'slug', 'is_active', 'created_at')
    list_filter = ('category', 'is_active')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'provider', 'category', 'venue_or_route', 'start_time', 'base_price', 'is_active')
    list_filter = ('category', 'provider', 'is_active')
    search_fields = ('title', 'venue_or_route')


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('ticket_ref', 'event', 'seat_or_coach', 'face_value', 'owner', 'status', 'created_at')
    list_filter = ('status', 'event__category')
    search_fields = ('ticket_ref', 'booking_reference', 'owner__username', 'attendee_name')


@admin.register(ResaleListing)
class ResaleListingAdmin(admin.ModelAdmin):
    list_display = ('id', 'ticket', 'seller', 'asking_price', 'service_fee', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('ticket__ticket_ref', 'seller__username')
