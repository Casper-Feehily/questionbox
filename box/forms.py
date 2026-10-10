from django import forms
from .graph import notifications_enabled


class AskForm(forms.Form):
    text = forms.CharField(label="想问点什么？", min_length=1, max_length=500,
                          widget=forms.Textarea(attrs={"placeholder": "从一个小小的好奇开始……", "rows": 6, "data-counter": "question-count", "data-max-length": 500}))
    signature = forms.CharField(label="你的署名（可选）", max_length=30, required=False,
                               widget=forms.TextInput(attrs={"placeholder": "留空就是匿名朋友", "autocomplete": "off"}))
    email = forms.EmailField(label="通知邮箱（可选）", required=False,
                            widget=forms.EmailInput(attrs={"placeholder": "you@example.com", "autocomplete": "email"}))
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Native maxlength counts UTF-16 units; use the same Unicode code-point count as Python.
        self.fields["text"].widget.attrs.pop("maxlength", None)

    def clean_email(self):
        value = self.cleaned_data["email"]
        if value and not notifications_enabled():
            raise forms.ValidationError("站点暂未启用邮件通知，请清空邮箱后提交。")
        return value.lower()

    def clean(self):
        cleaned = super().clean()
        if self.files:
            raise forms.ValidationError("仅接收文字，不接收图片或文件。")
        return cleaned


class AnswerForm(forms.Form):
    answer = forms.CharField(label="你的回答", max_length=10000,
                            widget=forms.Textarea(attrs={"rows": 12, "placeholder": "认真回答，慢慢聊。"}))
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
