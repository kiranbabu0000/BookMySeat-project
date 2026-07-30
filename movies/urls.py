from django.urls import path
from . import views
urlpatterns=[
    path('', views.movie_list, name='movie_list'),
    path('<int:movie_id>/', views.movie_detail, name='movie_detail'),
    path('<int:movie_id>/review/', views.submit_review, name='submit_review'),
    path('review/<int:review_id>/report/', views.report_review, name='report_review'),
    path('<int:movie_id>/theaters/', views.theater_list, name='theater_list'),
    path('theater/<int:theater_id>/seats/book/', views.book_seats, name='book_seats'),
]