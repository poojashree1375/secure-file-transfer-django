from django.contrib import admin

from .models import FileUpload


@admin.register(FileUpload)
class FileUploadAdmin(admin.ModelAdmin):
    list_display = (
        "file_name",
        "token",
        "uploaded_at",
        "expiry_datetime",
        "download_count",
        "max_downloads",
    )
    readonly_fields = ("token", "uploaded_at")
    search_fields = ("file_name", "token")
    list_filter = ("uploaded_at",)