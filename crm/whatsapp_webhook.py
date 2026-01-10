from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

VERIFY_TOKEN = "iconic_whatsapp_verify"

@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if token == VERIFY_TOKEN:
            return HttpResponse(challenge)
        return HttpResponse("Invalid token", status=403)

    if request.method == "POST":
        data = json.loads(request.body.decode("utf-8"))
        print("WhatsApp data:", data)
        return JsonResponse({"status": "received"})