from django import forms
import re
from .graph import notifications_enabled
from .models import Tag


class AskForm(forms.Form):
    text = forms.CharField(label="想问点什么？", min_length=1, max_length=700,
                          widget=forms.Textarea(attrs={"placeholder": "从一个小小的好奇开始……", "rows": 6, "data-counter": "question-count", "data-max-length": 700}))
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


class ClassificationForm(forms.Form):
    is_featured = forms.BooleanField(label="设为精选问答", required=False)
    tags = forms.ModelMultipleChoiceField(label="已有标签", queryset=Tag.objects.all(), required=False,
                                         widget=forms.CheckboxSelectMultiple)
    new_tags = forms.CharField(label="新标签", required=False, max_length=500,
                              widget=forms.TextInput(attrs={"placeholder": "例如：学习，生活；用逗号分隔"}))

    def clean_new_tags(self):
        names = list(dict.fromkeys(name.strip() for name in re.split(r"[,，\n]", self.cleaned_data["new_tags"]) if name.strip()))
        if any(len(name) > 30 for name in names):
            raise forms.ValidationError("每个标签最多 30 字。")
        return names


class AnswerForm(forms.Form):
    answer = forms.CharField(label="你的回答", max_length=10000,
                            widget=forms.Textarea(attrs={"rows": 12, "placeholder": "认真回答，慢慢聊。"}))
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    expected_draft = forms.CharField(required=False, max_length=36, widget=forms.HiddenInput)
    save_id = forms.UUIDField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.data.get("action") in {"draft", "autosave"}:
            self.fields["answer"].required = False
            self.fields["answer"].strip = False
