from decimal import Decimal
import secrets
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from accounts.models import User, Wallet, WalletTransaction, SetuKYCRecord
from catalog.models import TicketCategory
from orders.models import Order
from ticketing.models import Provider, Event, Ticket, ResaleListing


class Command(BaseCommand):
    help = 'Flushes existing seeded resale listings, test users, and orders, and seeds fresh available primary tickets across all categories.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clean-only',
            action='store_true',
            help='Only flushes all seeded data without creating new inventory.',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Flushing old seeded data and initializing clean direct ticket inventory...'))

        # 1. Purge all existing resale listings, orders, wallets, KYC records, and ALL users
        deleted_resales, _ = ResaleListing.objects.all().delete()
        deleted_orders, _ = Order.objects.all().delete()
        deleted_txs, _ = WalletTransaction.objects.all().delete()
        deleted_wallets, _ = Wallet.objects.all().delete()
        deleted_kycs, _ = SetuKYCRecord.objects.all().delete()
        deleted_users, _ = User.objects.all().delete()
        deleted_tickets, _ = Ticket.objects.all().delete()
        deleted_events, _ = Event.objects.all().delete()

        self.stdout.write(self.style.WARNING(
            f"Purged: {deleted_resales} resale listings, {deleted_orders} orders, "
            f"{deleted_txs} wallet transactions, {deleted_wallets} wallets, {deleted_kycs} KYC records, "
            f"{deleted_tickets} tickets, {deleted_events} events, {deleted_users} users (ALL)."
        ))

        if options.get('clean_only'):
            self.stdout.write(self.style.SUCCESS('Cleanup complete. No new inventory created.'))
            return

        # 2. Populate Ticket Categories
        categories_data = [
            {'name': 'Trains', 'slug': 'train', 'icon': 'Train', 'description': 'Vande Bharat, Rajdhani, Shatabdi, and superfast express tickets.'},
            {'name': 'Flights', 'slug': 'flight', 'icon': 'Plane', 'description': 'Domestic and international verified flight passes.'},
            {'name': 'Concerts & Festivals', 'slug': 'concert', 'icon': 'Music', 'description': 'Live music tours, stadium concerts, and multi-day music festivals.'},
            {'name': 'Movies', 'slug': 'movie', 'icon': 'Film', 'description': 'Cinema, IMAX 70mm, Laser 3D, and Director Cut showtimes.'},
            {'name': 'Festivals', 'slug': 'festival', 'icon': 'Sparkles', 'description': 'Multi-day music, arts, and cultural festivals.'},
            {'name': 'Sports', 'slug': 'sports', 'icon': 'Trophy', 'description': 'Cricket, football, tennis, and sporting tournaments.'},
            {'name': 'Other Events', 'slug': 'other', 'icon': 'Calendar', 'description': 'Theatrical plays, symphony orchestra, and fan conventions.'},
        ]

        for cat in categories_data:
            TicketCategory.objects.update_or_create(
                slug=cat['slug'],
                defaults={
                    'name': cat['name'],
                    'icon': cat['icon'],
                    'description': cat['description']
                }
            )

        # 3. Providers
        providers_data = [
            {
                'name': 'Indian Railways (IRCTC)',
                'slug': 'irctc',
                'category': 'train',
                'description': 'National railway operator offering Vande Bharat Express, Rajdhani, and Shatabdi express train routes across India.',
                'logo_url': 'https://images.unsplash.com/photo-1474487548417-781cb71495f3?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.irctc.co.in',
            },
            {
                'name': 'IndiGo Airlines',
                'slug': 'indigo',
                'category': 'flight',
                'description': "India's largest low-fare passenger airline providing reliable scheduled domestic and international air travel.",
                'logo_url': 'https://images.unsplash.com/photo-1436491865332-7a61a109cc05?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.goindigo.in',
            },
            {
                'name': 'Air India',
                'slug': 'air-india',
                'category': 'flight',
                'description': 'Full-service international and domestic flag carrier featuring widebody Dreamliner flights with premium hospitality.',
                'logo_url': 'https://images.unsplash.com/photo-1542296332-2e4473faf563?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.airindia.com',
            },
            {
                'name': 'PVR INOX Cinemas',
                'slug': 'pvr-inox',
                'category': 'movie',
                'description': 'Leading cinema chain in India featuring premium IMAX Laser, Dolby Atmos, and Director Cut recliner theatres.',
                'logo_url': 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.pvrcinemas.com',
            },
            {
                'name': 'Live Nation India',
                'slug': 'live-nation',
                'category': 'concert',
                'description': 'Global entertainment company organizing headline stadium tours, global pop concerts, and live symphony productions.',
                'logo_url': 'https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.livenation.com',
            },
            {
                'name': 'Sunburn Festival',
                'slug': 'sunburn',
                'category': 'festival',
                'description': "Asia's premier electronic dance music festival featuring world-class DJs and experiential seaside staging in Goa.",
                'logo_url': 'https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.sunburn.in',
            },
            {
                'name': 'Board of Control for Cricket in India (BCCI / IPL)',
                'slug': 'bcci-ipl',
                'category': 'sports',
                'description': 'Governing body for Indian cricket and organizer of the Indian Premier League and international bilateral series.',
                'logo_url': 'https://images.unsplash.com/photo-1540747913346-19e32dc3e97e?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.iplt20.com',
            },
            {
                'name': 'National Centre for the Performing Arts (NCPA)',
                'slug': 'ncpa',
                'category': 'other',
                'description': "India's premier cultural institution hosting symphony orchestra concerts, Broadway theatre, and classical recitals.",
                'logo_url': 'https://images.unsplash.com/photo-1507676184212-d03ab07a01bf?w=200&auto=format&fit=crop&q=80',
                'website': 'https://www.ncpamumbai.com',
            },
        ]

        provider_map = {}
        for p_data in providers_data:
            prov, _ = Provider.objects.update_or_create(
                slug=p_data['slug'],
                defaults=p_data
            )
            provider_map[prov.slug] = prov

        # 4. Events & High-Quality Inventory across Categories (ALL AVAILABLE, NO RESALES)
        events_data = [
            # ================= TRAINS =================
            {
                'key': 'train-vande-bharat-ndls-bsb',
                'provider_slug': 'irctc',
                'category': 'train',
                'title': 'Vande Bharat Express (22436): New Delhi → Varanasi',
                'venue_or_route': 'New Delhi Railway Station (NDLS) → Varanasi Junction (BSB)',
                'start_time': '2026-11-22T06:00:00Z',
                'end_time': '2026-11-22T14:00:00Z',
                'itinerary_or_details': 'High-Speed AC Semi-Sleeper • Platform 16 • 8h Duration • Onboard breakfast & lunch included',
                'image_url': 'https://images.unsplash.com/photo-1474487548417-781cb71495f3?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('1750.00'),
                'tickets': [
                    {'ref': 'IRCTC-VB22436-E1-11', 'seat': 'Executive Class Coach E1 • Seat 11 (Window)', 'price': Decimal('3200.00')},
                    {'ref': 'IRCTC-VB22436-E1-12', 'seat': 'Executive Class Coach E1 • Seat 12 (Aisle)', 'price': Decimal('3200.00')},
                    {'ref': 'IRCTC-VB22436-E1-14', 'seat': 'Executive Class Coach E1 • Seat 14 (Window)', 'price': Decimal('3200.00')},
                    {'ref': 'IRCTC-VB22436-E1-15', 'seat': 'Executive Class Coach E1 • Seat 15 (Aisle)', 'price': Decimal('3200.00')},
                    {'ref': 'IRCTC-VB22436-C3-32', 'seat': 'AC Chair Car Coach C3 • Seat 32 (Window)', 'price': Decimal('1750.00')},
                    {'ref': 'IRCTC-VB22436-C3-33', 'seat': 'AC Chair Car Coach C3 • Seat 33 (Aisle)', 'price': Decimal('1750.00')},
                    {'ref': 'IRCTC-VB22436-C4-45', 'seat': 'AC Chair Car Coach C4 • Seat 45 (Aisle)', 'price': Decimal('1750.00')},
                    {'ref': 'IRCTC-VB22436-C4-46', 'seat': 'AC Chair Car Coach C4 • Seat 46 (Middle)', 'price': Decimal('1750.00')},
                    {'ref': 'IRCTC-VB22436-C5-18', 'seat': 'AC Chair Car Coach C5 • Seat 18 (Window)', 'price': Decimal('1750.00')},
                ]
            },
            {
                'key': 'train-rajdhani-ndls-mmct',
                'provider_slug': 'irctc',
                'category': 'train',
                'title': 'Mumbai Rajdhani Express (12952): New Delhi → Mumbai Central',
                'venue_or_route': 'New Delhi (NDLS) → Mumbai Central (MMCT)',
                'start_time': '2026-11-28T16:55:00Z',
                'end_time': '2026-11-29T08:35:00Z',
                'itinerary_or_details': 'Superfast Overnight AC Express • Platform 3 • Complete bedding & multi-course dinner/breakfast included',
                'image_url': 'https://images.unsplash.com/photo-1532105956626-9569c03602f6?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('3850.00'),
                'tickets': [
                    {'ref': 'IRCTC-RAJ12952-H1-01', 'seat': 'AC 1st Class Coach H1 • Coupe Berth 01 (Lower)', 'price': Decimal('5400.00')},
                    {'ref': 'IRCTC-RAJ12952-H1-02', 'seat': 'AC 1st Class Coach H1 • Coupe Berth 02 (Upper)', 'price': Decimal('5400.00')},
                    {'ref': 'IRCTC-RAJ12952-A1-18', 'seat': 'AC 2-Tier Coach A1 • Berth 18 (Lower)', 'price': Decimal('3850.00')},
                    {'ref': 'IRCTC-RAJ12952-A1-19', 'seat': 'AC 2-Tier Coach A1 • Berth 19 (Upper)', 'price': Decimal('3850.00')},
                    {'ref': 'IRCTC-RAJ12952-A2-22', 'seat': 'AC 2-Tier Coach A2 • Berth 22 (Upper)', 'price': Decimal('3850.00')},
                    {'ref': 'IRCTC-RAJ12952-B1-12', 'seat': 'AC 3-Tier Coach B1 • Berth 12 (Lower)', 'price': Decimal('2450.00')},
                    {'ref': 'IRCTC-RAJ12952-B3-25', 'seat': 'AC 3-Tier Coach B3 • Berth 25 (Side Lower)', 'price': Decimal('2450.00')},
                    {'ref': 'IRCTC-RAJ12952-B3-26', 'seat': 'AC 3-Tier Coach B3 • Berth 26 (Side Upper)', 'price': Decimal('2450.00')},
                ]
            },
            {
                'key': 'train-vande-bharat-cst-goa',
                'provider_slug': 'irctc',
                'category': 'train',
                'title': 'Goa Vande Bharat Express (22229): Mumbai CST → Madgaon',
                'venue_or_route': 'Chhatrapati Shivaji Maharaj Terminus (CSMT) → Madgaon Junction (MAO)',
                'start_time': '2026-12-02T05:25:00Z',
                'end_time': '2026-12-02T13:10:00Z',
                'itinerary_or_details': 'Scenic Konkan Route • 7h 45m Non-stop Scenic Express • Gourmet Konkan breakfast included',
                'image_url': 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('1980.00'),
                'tickets': [
                    {'ref': 'IRCTC-GOA22229-EC-07', 'seat': 'Executive Class Coach EC1 • Seat 07 (Aisle Panorama)', 'price': Decimal('3450.00')},
                    {'ref': 'IRCTC-GOA22229-EC-08', 'seat': 'Executive Class Coach EC1 • Seat 08 (Window Panorama)', 'price': Decimal('3450.00')},
                    {'ref': 'IRCTC-GOA22229-CC-18', 'seat': 'AC Chair Car Coach C2 • Seat 18 (Middle)', 'price': Decimal('1980.00')},
                    {'ref': 'IRCTC-GOA22229-CC-19', 'seat': 'AC Chair Car Coach C2 • Seat 19 (Window)', 'price': Decimal('1980.00')},
                    {'ref': 'IRCTC-GOA22229-CC-20', 'seat': 'AC Chair Car Coach C2 • Seat 20 (Aisle)', 'price': Decimal('1980.00')},
                    {'ref': 'IRCTC-GOA22229-CC-24', 'seat': 'AC Chair Car Coach C3 • Seat 24 (Window)', 'price': Decimal('1980.00')},
                ]
            },

            # ================= FLIGHTS =================
            {
                'key': 'flight-indigo-bom-del',
                'provider_slug': 'indigo',
                'category': 'flight',
                'title': 'Flight 6E-204: Mumbai (BOM) → New Delhi (DEL)',
                'venue_or_route': 'Mumbai CSMIA (T2) → New Delhi IGI (T3)',
                'start_time': '2026-11-20T08:30:00Z',
                'end_time': '2026-11-20T10:45:00Z',
                'itinerary_or_details': 'Airbus A321neo • Non-stop 2h 15m • Terminal 2 Gate 42 • Economy Standard with 15kg checked baggage',
                'image_url': 'https://images.unsplash.com/photo-1436491865332-7a61a109cc05?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('4850.00'),
                'tickets': [
                    {'ref': 'IND-6E204-4A', 'seat': 'Seat 4A (Window • Extra Legroom Front Cabin)', 'price': Decimal('4850.00')},
                    {'ref': 'IND-6E204-4B', 'seat': 'Seat 4B (Middle • Forward Cabin)', 'price': Decimal('4850.00')},
                    {'ref': 'IND-6E204-12C', 'seat': 'Seat 12C (Aisle • Standard Economy)', 'price': Decimal('4850.00')},
                    {'ref': 'IND-6E204-12D', 'seat': 'Seat 12D (Aisle • Standard Economy)', 'price': Decimal('4850.00')},
                    {'ref': 'IND-6E204-18D', 'seat': 'Seat 18D (Aisle • Standard Economy)', 'price': Decimal('4850.00')},
                    {'ref': 'IND-6E204-21F', 'seat': 'Seat 21F (Window • Rear Cabin)', 'price': Decimal('4850.00')},
                ]
            },
            {
                'key': 'flight-indigo-blr-goa',
                'provider_slug': 'indigo',
                'category': 'flight',
                'title': 'Flight 6E-534: Bengaluru (BLR) → Goa (GOX)',
                'venue_or_route': 'Bengaluru Kempegowda (T1) → Manohar Mopa Airport (GOX)',
                'start_time': '2026-11-25T14:15:00Z',
                'end_time': '2026-11-25T15:30:00Z',
                'itinerary_or_details': 'Airbus A320 • Non-stop 1h 15m • Terminal 1 Gate 18 • Economy Regular',
                'image_url': 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('3499.00'),
                'tickets': [
                    {'ref': 'IND-6E534-3A', 'seat': 'Seat 3A (Window • Front Cabin)', 'price': Decimal('3499.00')},
                    {'ref': 'IND-6E534-3B', 'seat': 'Seat 3B (Middle • Front Cabin)', 'price': Decimal('3499.00')},
                    {'ref': 'IND-6E534-8C', 'seat': 'Seat 8C (Aisle • Standard Economy)', 'price': Decimal('3499.00')},
                    {'ref': 'IND-6E534-8D', 'seat': 'Seat 8D (Aisle • Standard Economy)', 'price': Decimal('3499.00')},
                    {'ref': 'IND-6E534-15E', 'seat': 'Seat 15E (Middle • Standard Economy)', 'price': Decimal('3499.00')},
                    {'ref': 'IND-6E534-19A', 'seat': 'Seat 19A (Window • Rear Cabin)', 'price': Decimal('3499.00')},
                ]
            },
            {
                'key': 'flight-ai-del-bom',
                'provider_slug': 'air-india',
                'category': 'flight',
                'title': 'Flight AI-806: New Delhi (DEL) → Mumbai (BOM)',
                'venue_or_route': 'New Delhi IGI (T3) → Mumbai CSMIA (T2)',
                'start_time': '2026-12-05T18:00:00Z',
                'end_time': '2026-12-05T20:20:00Z',
                'itinerary_or_details': 'Boeing 787-8 Dreamliner • Non-stop 2h 20m • Terminal 3 Gate 31 • Full Meal Service Included',
                'image_url': 'https://images.unsplash.com/photo-1542296332-2e4473faf563?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('5600.00'),
                'tickets': [
                    {'ref': 'AI-806-02A', 'seat': 'Seat 2A (Window • Business Class Suite)', 'price': Decimal('14500.00')},
                    {'ref': 'AI-806-02K', 'seat': 'Seat 2K (Window • Business Class Suite)', 'price': Decimal('14500.00')},
                    {'ref': 'AI-806-11A', 'seat': 'Seat 11A (Window • Forward Economy)', 'price': Decimal('5600.00')},
                    {'ref': 'AI-806-11B', 'seat': 'Seat 11B (Middle • Forward Economy)', 'price': Decimal('5600.00')},
                    {'ref': 'AI-806-24J', 'seat': 'Seat 24J (Aisle • Twin Aisle Widebody)', 'price': Decimal('5600.00')},
                    {'ref': 'AI-806-24K', 'seat': 'Seat 24K (Window • Twin Aisle Widebody)', 'price': Decimal('5600.00')},
                ]
            },

            # ================= CONCERTS & FESTIVALS =================
            {
                'key': 'concert-coldplay-dy-patil',
                'provider_slug': 'live-nation',
                'category': 'concert',
                'title': 'Coldplay: Music of the Spheres World Tour',
                'venue_or_route': 'DY Patil Sports Stadium, Nerul, Navi Mumbai',
                'start_time': '2026-11-15T18:30:00Z',
                'end_time': '2026-11-15T22:30:00Z',
                'itinerary_or_details': 'Gate 4 Entry • South Stand Level 2 Bay 18 • Kinetic dance floor and synchronized LED wristband included',
                'image_url': 'https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('6500.00'),
                'tickets': [
                    {'ref': 'LN-CP-FLR-88', 'seat': 'Ground Floor Standing • General Admission Area (Pass #88)', 'price': Decimal('4500.00')},
                    {'ref': 'LN-CP-FLR-89', 'seat': 'Ground Floor Standing • General Admission Area (Pass #89)', 'price': Decimal('4500.00')},
                    {'ref': 'LN-CP-S2-C12', 'seat': 'South Stand Level 2 • Bay 18 Row C Seat 12', 'price': Decimal('6500.00')},
                    {'ref': 'LN-CP-S2-C13', 'seat': 'South Stand Level 2 • Bay 18 Row C Seat 13', 'price': Decimal('6500.00')},
                    {'ref': 'LN-CP-S2-C14', 'seat': 'South Stand Level 2 • Bay 18 Row C Seat 14', 'price': Decimal('6500.00')},
                    {'ref': 'LN-CP-VIP-22', 'seat': 'VIP Lounge Deck • Bay 4 Pass 22 (Hospitality Included)', 'price': Decimal('12500.00')},
                    {'ref': 'LN-CP-VIP-23', 'seat': 'VIP Lounge Deck • Bay 4 Pass 23 (Hospitality Included)', 'price': Decimal('12500.00')},
                    {'ref': 'LN-CP-W1-B05', 'seat': 'West Stand Level 1 • Row B Seat 05', 'price': Decimal('5500.00')},
                ]
            },
            {
                'key': 'concert-diljit-delhi',
                'provider_slug': 'live-nation',
                'category': 'concert',
                'title': 'Diljit Dosanjh: Dil-Luminati India Tour',
                'venue_or_route': 'Bharat Mandapam Open Arena, Pragati Maidan, New Delhi',
                'start_time': '2026-12-08T18:00:00Z',
                'end_time': '2026-12-08T22:00:00Z',
                'itinerary_or_details': 'Gate 6 Entry • Fan Pit Stage Front • Synchronized Light Show • Exclusive Commemorative Tour Laminate',
                'image_url': 'https://images.unsplash.com/photo-1516450360452-9312f5e86fc7?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('5999.00'),
                'tickets': [
                    {'ref': 'LN-DIL-PIT-07', 'seat': 'Fan Pit Platinum • Stage Front Center Pass #07', 'price': Decimal('9999.00')},
                    {'ref': 'LN-DIL-PIT-08', 'seat': 'Fan Pit Platinum • Stage Front Center Pass #08', 'price': Decimal('9999.00')},
                    {'ref': 'LN-DIL-GLD-41', 'seat': 'Gold Tier 1 • Row G Seat 41', 'price': Decimal('5999.00')},
                    {'ref': 'LN-DIL-GLD-42', 'seat': 'Gold Tier 1 • Row G Seat 42', 'price': Decimal('5999.00')},
                    {'ref': 'LN-DIL-GLD-43', 'seat': 'Gold Tier 1 • Row G Seat 43', 'price': Decimal('5999.00')},
                    {'ref': 'LN-DIL-SLV-90', 'seat': 'Silver General Admission • Pass #90', 'price': Decimal('3499.00')},
                ]
            },
            {
                'key': 'festival-sunburn-goa',
                'provider_slug': 'sunburn',
                'category': 'festival',
                'title': 'Sunburn Goa 2026: 3-Day Seaside EDM Festival Pass',
                'venue_or_route': 'Vagator Beach Arena Grounds, North Goa',
                'start_time': '2026-12-28T15:00:00Z',
                'end_time': '2026-12-30T23:59:00Z',
                'itinerary_or_details': '3-Day Full Festival Pass • RFID Fast-Track Wristband • Access to all 5 stages across techno, EDM & house',
                'image_url': 'https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('4999.00'),
                'tickets': [
                    {'ref': 'SB-GOA26-GA-9011', 'seat': 'General Admission • 3-Day All-Access Pass #SB-9011', 'price': Decimal('4999.00')},
                    {'ref': 'SB-GOA26-GA-9012', 'seat': 'General Admission • 3-Day All-Access Pass #SB-9012', 'price': Decimal('4999.00')},
                    {'ref': 'SB-GOA26-GA-9015', 'seat': 'General Admission • 3-Day All-Access Pass #SB-9015', 'price': Decimal('4999.00')},
                    {'ref': 'SB-GOA26-VIP-401', 'seat': 'VIP Elevated Deck • 3-Day Fast-Track Pass #SB-VIP-401', 'price': Decimal('9500.00')},
                    {'ref': 'SB-GOA26-VIP-402', 'seat': 'VIP Elevated Deck • 3-Day Fast-Track Pass #SB-VIP-402', 'price': Decimal('9500.00')},
                ]
            },

            # ================= MOVIES =================
            {
                'key': 'movie-interstellar-imax',
                'provider_slug': 'pvr-inox',
                'category': 'movie',
                'title': 'Interstellar: 10th Anniversary IMAX 70mm Special Screening',
                'venue_or_route': 'PVR IMAX, Phoenix Palladium, Lower Parel, Mumbai',
                'start_time': '2026-11-21T19:15:00Z',
                'end_time': '2026-11-21T22:30:00Z',
                'itinerary_or_details': 'Screen 2 • Exclusive 70mm Analog IMAX Film Print Presentation • Uncompressed Sound Master',
                'image_url': 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('850.00'),
                'tickets': [
                    {'ref': 'PVR-INT-D08', 'seat': 'Screen 2 IMAX • Classic Row D - Seat 8', 'price': Decimal('550.00')},
                    {'ref': 'PVR-INT-G14', 'seat': 'Screen 2 IMAX • Premium Row G - Seat 14 (Director Sweetspot)', 'price': Decimal('850.00')},
                    {'ref': 'PVR-INT-G15', 'seat': 'Screen 2 IMAX • Premium Row G - Seat 15 (Director Sweetspot)', 'price': Decimal('850.00')},
                    {'ref': 'PVR-INT-F11', 'seat': 'Screen 2 IMAX • Premium Row F - Seat 11 (Center Aisle)', 'price': Decimal('850.00')},
                    {'ref': 'PVR-INT-H02', 'seat': 'Screen 2 IMAX • Luxury Recliner Row H - Seat 2', 'price': Decimal('1150.00')},
                    {'ref': 'PVR-INT-H03', 'seat': 'Screen 2 IMAX • Luxury Recliner Row H - Seat 3', 'price': Decimal('1150.00')},
                ]
            },
            {
                'key': 'movie-avatar-director-cut',
                'provider_slug': 'pvr-inox',
                'category': 'movie',
                'title': 'Avatar: Fire and Ash — IMAX 3D Premiere',
                'venue_or_route': "PVR Director's Cut, Ambience Mall, Vasant Kunj, New Delhi",
                'start_time': '2026-11-18T20:00:00Z',
                'end_time': '2026-11-18T23:15:00Z',
                'itinerary_or_details': 'Audi 1 • IMAX with Laser 3D & 12-Channel Immersive Sound • Luxury Leather Recliner with Butler Service',
                'image_url': 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('950.00'),
                'tickets': [
                    {'ref': 'PVR-AVT-F05', 'seat': 'Audi 1 Laser IMAX • Executive Row F - Seat 5', 'price': Decimal('650.00')},
                    {'ref': 'PVR-AVT-H07', 'seat': 'Audi 1 Laser IMAX • Recliner Row H - Seat 7 (Prime Center)', 'price': Decimal('950.00')},
                    {'ref': 'PVR-AVT-H08', 'seat': 'Audi 1 Laser IMAX • Recliner Row H - Seat 8 (Prime Center)', 'price': Decimal('950.00')},
                    {'ref': 'PVR-AVT-J11', 'seat': 'Audi 1 Laser IMAX • Recliner Row J - Seat 11', 'price': Decimal('950.00')},
                    {'ref': 'PVR-AVT-J12', 'seat': 'Audi 1 Laser IMAX • Recliner Row J - Seat 12', 'price': Decimal('950.00')},
                ]
            },
            {
                'key': 'movie-oppenheimer-imax',
                'provider_slug': 'pvr-inox',
                'category': 'movie',
                'title': 'Oppenheimer: The 70mm IMAX Experience',
                'venue_or_route': 'PVR IMAX, Phoenix Marketcity, Whitefield, Bengaluru',
                'start_time': '2026-11-26T18:45:00Z',
                'end_time': '2026-11-26T22:00:00Z',
                'itinerary_or_details': 'Audi 3 • 70mm High-Resolution Film Print • Full Aspect Ratio 1.43:1 Presentation',
                'image_url': 'https://images.unsplash.com/photo-1536440136628-849c177e76a1?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('750.00'),
                'tickets': [
                    {'ref': 'PVR-OPP-E10', 'seat': 'Audi 3 IMAX • Prime Row E - Seat 10', 'price': Decimal('750.00')},
                    {'ref': 'PVR-OPP-E11', 'seat': 'Audi 3 IMAX • Prime Row E - Seat 11', 'price': Decimal('750.00')},
                    {'ref': 'PVR-OPP-E12', 'seat': 'Audi 3 IMAX • Prime Row E - Seat 12', 'price': Decimal('750.00')},
                    {'ref': 'PVR-OPP-F14', 'seat': 'Audi 3 IMAX • Recliner Row F - Seat 14', 'price': Decimal('980.00')},
                    {'ref': 'PVR-OPP-F15', 'seat': 'Audi 3 IMAX • Recliner Row F - Seat 15', 'price': Decimal('980.00')},
                ]
            },

            # ================= SPORTS & OTHER =================
            {
                'key': 'sports-ipl-mi-vs-csk',
                'provider_slug': 'bcci-ipl',
                'category': 'sports',
                'title': 'IPL 2026: Mumbai Indians vs Chennai Super Kings',
                'venue_or_route': 'Wankhede Stadium, Churchgate, Mumbai',
                'start_time': '2026-11-12T19:30:00Z',
                'end_time': '2026-11-12T23:30:00Z',
                'itinerary_or_details': 'Gate 3 Access • Garware Pavilion Level 2 • Complimentary beverage voucher & team fan flag',
                'image_url': 'https://images.unsplash.com/photo-1540747913346-19e32dc3e97e?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('3500.00'),
                'tickets': [
                    {'ref': 'BCCI-IPL-GAR-D14', 'seat': 'Garware Pavilion Level 2 • Row D Seat 14', 'price': Decimal('3500.00')},
                    {'ref': 'BCCI-IPL-GAR-D15', 'seat': 'Garware Pavilion Level 2 • Row D Seat 15', 'price': Decimal('3500.00')},
                    {'ref': 'BCCI-IPL-SAC-G21', 'seat': 'Sachin Tendulkar Stand Tier 1 • Row G Seat 21', 'price': Decimal('2200.00')},
                    {'ref': 'BCCI-IPL-SAC-G22', 'seat': 'Sachin Tendulkar Stand Tier 1 • Row G Seat 22', 'price': Decimal('2200.00')},
                    {'ref': 'BCCI-IPL-NOR-C10', 'seat': 'North Stand Level 1 • Row C Seat 10', 'price': Decimal('1800.00')},
                    {'ref': 'BCCI-IPL-NOR-C11', 'seat': 'North Stand Level 1 • Row C Seat 11', 'price': Decimal('1800.00')},
                ]
            },
            {
                'key': 'other-ncpa-beethoven-ninth',
                'provider_slug': 'ncpa',
                'category': 'other',
                'title': "Symphony Orchestra of India: Beethoven's Ninth Symphony",
                'venue_or_route': 'Jamshed Bhabha Theatre, NCPA, Nariman Point, Mumbai',
                'start_time': '2026-11-19T19:00:00Z',
                'end_time': '2026-11-19T21:30:00Z',
                'itinerary_or_details': 'Dress Circle Center • Featuring international choir and acclaimed soloists • Formal evening attire requested',
                'image_url': 'https://images.unsplash.com/photo-1507676184212-d03ab07a01bf?w=800&auto=format&fit=crop&q=80',
                'base_price': Decimal('1800.00'),
                'tickets': [
                    {'ref': 'NCPA-SOI-STL-F17', 'seat': 'Stalls Tier 1 • Row F Seat 17', 'price': Decimal('1200.00')},
                    {'ref': 'NCPA-SOI-STL-F18', 'seat': 'Stalls Tier 1 • Row F Seat 18', 'price': Decimal('1200.00')},
                    {'ref': 'NCPA-SOI-STL-F19', 'seat': 'Stalls Tier 1 • Row F Seat 19', 'price': Decimal('1200.00')},
                    {'ref': 'NCPA-SOI-DC-D09', 'seat': 'Dress Circle Center • Row D Seat 09', 'price': Decimal('1800.00')},
                    {'ref': 'NCPA-SOI-DC-D10', 'seat': 'Dress Circle Center • Row D Seat 10', 'price': Decimal('1800.00')},
                    {'ref': 'NCPA-SOI-DC-D11', 'seat': 'Dress Circle Center • Row D Seat 11', 'price': Decimal('1800.00')},
                ]
            },
        ]

        total_seeded_tickets = 0

        for ed in events_data:
            prov = provider_map[ed['provider_slug']]
            start_dt = parse_datetime(ed['start_time'])
            end_dt = parse_datetime(ed['end_time']) if ed.get('end_time') else None

            event, _ = Event.objects.update_or_create(
                title=ed['title'],
                provider=prov,
                defaults={
                    'category': ed['category'],
                    'venue_or_route': ed['venue_or_route'],
                    'start_time': start_dt,
                    'end_time': end_dt,
                    'itinerary_or_details': ed['itinerary_or_details'],
                    'image_url': ed['image_url'],
                    'base_price': ed['base_price'],
                    'is_active': True,
                }
            )

            for td in ed['tickets']:
                ref = td['ref']
                Ticket.objects.update_or_create(
                    ticket_ref=ref,
                    defaults={
                        'event': event,
                        'seat_or_coach': td['seat'],
                        'face_value': td['price'],
                        'status': 'AVAILABLE',
                        'owner': None,
                        'attendee_name': '',
                        'booking_reference': f"BKG-{secrets.token_hex(4).upper()}",
                        'reserved_by': None,
                        'reserved_until': None,
                        'qr_token': None,
                        'qr_token_hash': '',
                    }
                )
                total_seeded_tickets += 1

        # Confirm strictly ZERO resale listings and ZERO pre-owned tickets
        resale_count = ResaleListing.objects.count()
        owned_count = Ticket.objects.filter(owner__isnull=False).count()

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded database! "
            f"Seeded {len(providers_data)} providers, {len(events_data)} events, "
            f"and {total_seeded_tickets} 100% AVAILABLE direct tickets. "
            f"Active Resale Listings: {resale_count} (strictly zero). "
            f"Pre-owned Tickets: {owned_count} (strictly zero). "
            f"All inventory is fresh and ready for user bookings!"
        ))
