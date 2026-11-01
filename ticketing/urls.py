from django.urls import path
from .views import (
    list_user_tickets,
    ticket_detail,
    list_eligible_resale_tickets,
    download_ticket_pdf_view,
)

urlpatterns = [
    path('mine/', list_user_tickets, name='ticket-mine'),
    path('eligible-to-sell/', list_eligible_resale_tickets, name='ticket-eligible-resale'),
    path('<int:pk>/', ticket_detail, name='ticket-detail'),
    path('<int:pk>/pdf/', download_ticket_pdf_view, name='ticket-pdf'),
]
