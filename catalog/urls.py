from django.urls import path
from .views import (
    list_categories,
    list_providers,
    list_marketplace_listings,
    get_listing_detail,
    create_resale_listing,
    list_seller_listings,
    withdraw_resale_listing,
)

urlpatterns = [
    path('categories/', list_categories, name='catalog-categories'),
    path('providers/', list_providers, name='catalog-providers'),
    path('listings/', list_marketplace_listings, name='catalog-listings'),
    path('listings/<str:pk>/', get_listing_detail, name='catalog-listing-detail'),
    path('resale/create/', create_resale_listing, name='catalog-resale-create'),
    path('resale/my-listings/', list_seller_listings, name='catalog-resale-my-listings'),
    path('listings/<str:pk>/withdraw/', withdraw_resale_listing, name='catalog-listing-withdraw'),
]
