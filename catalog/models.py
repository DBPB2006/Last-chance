from django.db import models


class TicketCategory(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    description = models.TextField(blank=True, default='')
    icon = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        verbose_name_plural = 'Ticket Categories'
        ordering = ['name']

    def __str__(self):
        return self.name
