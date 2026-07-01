from django.urls import path
from . import views

urlpatterns=[
    path('',views.upload_file,name="upload"),
    path('link/<str:token>/',views.show_link,name="show_link"),
    path('download/<str:token>/',views.download_file,name="download_file"),
]