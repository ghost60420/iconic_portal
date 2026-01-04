from django.db import models
from django.contrib.auth import get_user_model
from crm.models import Lead, Opportunity

User = get_user_model()


class AIAgent(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    role = models.CharField(max_length=100)
    category = models.CharField(max_length=100)
    description = models.TextField()
    system_prompt = models.TextField()

    def __str__(self):
        return self.name


class AIConversation(models.Model):
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(
        Opportunity, null=True, blank=True, on_delete=models.CASCADE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.lead:
            return f"{self.agent.name} with {self.lead.account_brand}"
        return f"{self.agent.name} conversation {self.id}"


class AIMessage(models.Model):
    conversation = models.ForeignKey(
        AIConversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.CharField(max_length=10)  # "user" or "ai"
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender} at {self.created_at}"