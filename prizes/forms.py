from django import forms


ANIME_CHOICES = [
    ("鬼滅の刃", "鬼滅の刃"),
    ("葬送のフリーレン", "葬送のフリーレン"),
    ("薬屋のひとりごと", "薬屋のひとりごと"),
    ("僕のヒーローアカデミア", "僕のヒーローアカデミア"),
    ("呪術廻戦", "呪術廻戦"),
    ("ワンピース", "ワンピース"),
    ("推しの子", "推しの子"),
    ("スパイファミリー", "スパイファミリー"),
]


class PrizeSearchForm(forms.Form):
    keywords = forms.MultipleChoiceField(
        label="アニメ作品",
        choices=ANIME_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        error_messages={"required": "少なくとも1作品を選択してください。"},
    )
    start_date = forms.DateField(
        label="開始日",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="終了日",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError("開始日は終了日以前にしてください。")
        return cleaned_data
