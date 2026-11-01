# LastChance: Peer-to-Peer (P2P) Ticket Reselling & Transfer Platform

**LastChance** is a secure, traceable peer-to-peer (P2P) ticket reselling and digital transfer platform built with **Django**.

The platform demonstrates a safer secondary ticket marketplace that eliminates ticket fraud, scalping, and double-selling by combining mandatory government-grade identity verification (Setu DigiLocker), strict anti-scalping price caps (≤120% of face value), atomic inventory reservations, Razorpay payment processing, and cryptographic QR credential rotation upon ownership transfer.

---

## 1. Core Platform Architecture

### 1. Direct Booking & Provider Inventory
- Pre-created inventory across 7 supported ticket categories: **Trains**, **Flights**, **Movies**, **Concerts**, **Festivals**, **Sports**, and **Other Events**.
- Available primary tickets (`status='AVAILABLE'`) can be booked directly through the platform.

### 2. Mandatory Identity Verification (Setu DigiLocker KYC)
- Legal identity verification via **Setu DigiLocker** is strictly required for **both buyers and sellers** across all transactions.
- **Selling Prerequisite**: Sellers must complete verified DigiLocker authentication before listing or reactivating tickets for peer resale.
- **Buying Prerequisite**: Buyers must be identity-verified before reserving inventory or completing checkout.
- Authoritative KYC status (`is_identity_verified` and `verified_legal_name`) is validated at every transaction boundary.

### 3. Anti-Scalping Rules & Resale Listing
- Verified ticket owners can offer unneeded tickets from their **Ticket Vault** for resale.
- Asking prices are strictly capped at **maximum 120% of original face value** (enforced at the database and API level).
- When listed, the ticket transitions to `LISTED` status and becomes visible in the public marketplace.

### 4. Atomic Reservations & Razorpay Payment Verification
- When a buyer initiates checkout (`/checkout/<id>/`), the system atomically locks the ticket row for 10 minutes (`select_for_update()`) to prevent race conditions or double-booking.
- Payment is processed through Razorpay Standard Checkout.
- The server cryptographically verifies payment using HMAC SHA-256 signatures and authoritatively confirms captured amount with the Razorpay API.

### 5. Ownership Transfer & Cryptographic QR Rotation
- Upon verified payment:
  - The ticket's ownership is atomically transferred to the buyer (`ticket.owner = buyer`).
  - The resale listing is marked as `SOLD`.
  - The seller's wallet is credited with the resale payout in the platform's escrow ledger (`/wallet/`).
  - **The seller's old QR credential is permanently invalidated.**
  - A **brand-new cryptographic QR credential** is generated exclusively for the buyer.
  - The seller is strictly locked out from viewing or downloading the ticket pass.

### 6. Dynamic In-Memory Passes & PDF Generation
- No ticket images or files are saved to the server disk.
- High-resolution QR codes are dynamically generated in memory as Base64 data URIs (`get_qr_image_base64()`).
- Official digital admission passes can be downloaded on demand as clean, formatted PDF documents (`/tickets/<id>/download/`) generated on-the-fly using `Pillow`.

### 7. Gate Scanner & QR Validation
- Venue staff or turnstiles can scan the ticket's QR code (`/ticket/<credential>/`) to verify authenticity, event details, attendee name, and admission status in real time.

---

## 2. Technology Stack

- **Backend Framework**: Python 3.11+, Django 5.1+
- **Frontend / Templating**: Django Template Language (DTL) with a Vanilla CSS design system
- **REST APIs**: Django REST Framework (DRF) — endpoints preserved under `/api/`
- **Database**: SQLite (`db.sqlite3`) with WAL journal mode
- **Payment Gateway**: Razorpay Standard Checkout & Orders API with HMAC SHA-256 verification
- **Identity Verification**: Setu DigiLocker Sandbox API integration
- **Pass & PDF Generation**: `qrcode` and `Pillow` (100% in-memory generation)

---

## 3. Directory Structure

```text
ticket-resell/
├── manage.py                    # Django management script
├── config/                      # Core project settings, root URLs, middleware
│   ├── settings.py              # Security, database, and integration settings
│   ├── urls.py                  # Root URL routing configuration
│   ├── middleware.py            # Security headers & custom 404 handling
│   └── views.py                 # Health checks & global error views
├── accounts/                    # User authentication, wallet, and Setu DigiLocker KYC
│   ├── models.py                # User, SetuKYCRecord, Wallet, WalletTransaction
│   ├── setu_client.py           # Setu DigiLocker Sandbox API client
│   ├── views.py                 # Login, register, KYC flow, wallet views
│   └── urls.py                  # Account & KYC routes
├── catalog/                     # Inventory discovery, marketplace, and resale management
│   ├── views.py                 # Explore, listing detail, sell, and withdrawal views
│   └── urls.py                  # Catalog routes
├── orders/                      # Checkout, reservations, and Razorpay payment verification
│   ├── models.py                # Order and Payment models
│   ├── views.py                 # Checkout, payment verification, order history
│   └── urls.py                  # Order & payment routes
├── ticketing/                   # Providers, events, tickets, and pass issuance
│   ├── models.py                # Provider, Event, Ticket, ResaleListing
│   ├── views.py                 # Ticket Vault, digital pass view, PDF generation
│   ├── urls.py                  # Ticketing & QR gate validation routes
│   └── management/commands/     # Seed inventory management command
├── templates/                   # Clean Django Template Language (DTL) templates
│   ├── base.html                # Responsive layout shell & navigation
│   ├── 404.html                 # Custom not-found error page
│   ├── accounts/                # Login, register, verification, wallet templates
│   ├── catalog/                 # Explore, listing detail, sell, my listings templates
│   ├── orders/                  # Checkout and order history templates
│   └── ticketing/               # Vault, digital pass, and invalid pass templates
├── static/                      # Static CSS stylesheets and UI image assets
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment configuration template
├── .gitignore                   # Version control ignore rules
└── README.md                    # Project documentation
```

---

## 4. Setup and Installation

### Prerequisites
- Python 3.11 or higher
- SQLite 3

### Step-by-Step Setup
```bash
# 1. Navigate to the project directory
cd ticket-resell

# 2. Create and activate a Python virtual environment
python3 -m venv myenv
source myenv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment configuration
cp .env.example .env

# 5. Run database migrations
python manage.py migrate

# 6. Seed initial providers, routes, venues, and direct-booking tickets
python manage.py seed_inventory

# 7. Start the development server
python manage.py runserver 127.0.0.1:8000
```

The application will be live at **http://127.0.0.1:8000/**.

---

## 5. Environment Configuration

Configure `.env` with your API credentials:

```env
SECRET_KEY=django-insecure-tsr-dev-secret-key-safe-local
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,testserver

# Setu DigiLocker Sandbox Configuration
SETU_BASE_URL=https://dg-sandbox.setu.co
SETU_CLIENT_ID=<your_setu_client_id>
SETU_CLIENT_SECRET=<your_setu_client_secret>
SETU_PRODUCT_INSTANCE_ID=<your_product_instance_id>
SETU_REDIRECT_URL=http://127.0.0.1:8000/kyc/callback/

# Razorpay Payment Gateway Configuration
RAZORPAY_KEY_ID=<your_razorpay_key_id>
RAZORPAY_KEY_SECRET=<your_razorpay_key_secret>
```

---

## 6. Complete Routes Summary

### Django Template Web Pages
| Route | Method | Description |
|---|---|---|
| `/` | `GET` | Marketplace discovery, category filtering, search, and sorting |
| `/listings/<id>/` | `GET` | Direct inventory or resale listing detail view with seat grid |
| `/checkout/<id>/` | `GET, POST` | 10-minute atomic inventory hold and Razorpay checkout |
| `/checkout/<order_id>/verify/` | `GET, POST` | Cryptographic payment verification and ownership transfer |
| `/vault/` | `GET` | User's digital Ticket Vault displaying owned active tickets |
| `/tickets/<id>/` | `GET` | Full digital ticket pass with in-memory QR code |
| `/tickets/<id>/download/` | `GET` | Download official PDF admission pass on-demand |
| `/sell/` | `GET, POST` | List an owned ticket for resale (enforces DigiLocker KYC) |
| `/my-listings/` | `GET` | Current user's resale listings with status indicators |
| `/my-listings/<id>/withdraw/` | `POST` | Withdraw an active resale listing back to active ticket status |
| `/orders/` | `GET` | Chronological purchase history for the authenticated user |
| `/wallet/` | `GET` | Resale earnings, wallet balance, and credit transaction ledger |
| `/ticket/<credential>/` | `GET` | Real-time QR gate scanner and admission validation |
| `/login/` | `GET, POST` | User session login (supports username or email) |
| `/register/` | `GET, POST` | Account creation with automatic login |
| `/logout/` | `POST` | User session logout |
| `/verification/` | `GET, POST` | Setu DigiLocker identity verification portal |
| `/kyc/callback/` | `GET` | Authoritative DigiLocker redirect handler and identity sync |
| `/admin/` | `GET, POST` | Authoritative Django Admin management portal |

### Preserved REST API Endpoints (`/api/`)
| Route | Method | Description |
|---|---|---|
| `/api/health/` | `GET` | Health check (Database, Setu, Razorpay status) |
| `/api/auth/register/` | `POST` | User registration |
| `/api/auth/login/` | `POST` | User login |
| `/api/auth/logout/` | `POST` | User logout |
| `/api/auth/me/` | `GET` | Current user profile and KYC status |
| `/api/auth/wallet/` | `GET` | Authenticated user wallet balance & transactions |
| `/api/auth/setu/initiate/` | `POST` | Initiate Setu DigiLocker consent request |
| `/api/auth/setu/status/` | `GET` | Poll Setu KYC status |
| `/api/catalog/categories/` | `GET` | Supported ticket categories list |
| `/api/catalog/providers/` | `GET` | Active providers directory |
| `/api/catalog/listings/` | `GET` | Marketplace search and filter endpoint |
| `/api/catalog/listings/<id>/` | `GET` | Listing detail endpoint |
| `/api/catalog/resale/create/` | `POST` | Create peer resale listing |
| `/api/catalog/resale/my-listings/` | `GET` | Authenticated seller's resale listings |
| `/api/catalog/listings/<id>/withdraw/` | `POST` | Withdraw resale listing |
| `/api/orders/checkout/` | `POST` | Create checkout reservation and Razorpay order |
| `/api/orders/<id>/verify-payment/` | `POST` | Verify payment and transfer ticket |
| `/api/orders/history/` | `GET` | Buyer order history |
| `/api/ticketing/mine/` | `GET` | Owned tickets list |
| `/api/ticketing/<id>/` | `GET` | Owned ticket detail and QR pass |
| `/api/ticketing/<id>/pdf/` | `GET` | Download PDF pass for owned ticket |
| `/api/ticketing/eligible-to-sell/` | `GET` | Resale-eligible owned tickets |

---

## 7. Testing & Verification

Run the automated test suite:
```bash
python manage.py test
```

All 20 automated tests validate permissions, mandatory KYC enforcement for buyers and sellers, 120% resale price caps, atomic reservations, Razorpay signature verification, credential rotation, owner-isolated PDF downloads, and DTL web views.
