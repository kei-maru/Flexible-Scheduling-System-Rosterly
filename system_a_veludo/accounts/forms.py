# accounts/forms.py

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

# 引入模型
from casts.models import CastProfile, CastMedia

User = get_user_model()

# ==========================================
# 1. 登录与注册表单 (保持不变)
# ==========================================

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        # 根据你的HTML，这里包含了 User 模型字段
        fields = ['vrc_id', 'email', 'discord_id', 'twitter_id', 'avatar'] 

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # --- 统一添加样式 ---
        for field_name, field in self.fields.items():
            # 基础样式
            field.widget.attrs['class'] = 'input-field'
            
            # [需求 1] 针对头像字段：
            # 使用 FileInput 覆盖默认的 ClearableFileInput
            # 这样就不会显示 "目前: /media/..." 的文字，只显示上传按钮
            if field_name == 'avatar':
                field.widget = forms.FileInput(attrs={
                    'class': 'block w-full text-xs text-gray-400 file:mr-4 file:py-2 file:px-4 file:border-0 file:text-xs file:bg-white/10 file:text-veludo-accent-gold hover:file:bg-white/20 cursor-pointer',
                    'accept': 'image/*'
                })

        # Discord ID 通常是只读的
        if 'discord_id' in self.fields:
            self.fields['discord_id'].widget.attrs['readonly'] = True
            self.fields['discord_id'].widget.attrs['class'] += ' opacity-50 cursor-not-allowed'

class VeludoLoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': 'input-field', 'id': 'username', 'autocomplete': 'username'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'input-field', 'id': 'password', 'autocomplete': 'current-password'
    }))
from django.core.exceptions import ValidationError

class VeludoRegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'input-field'
            field.widget.attrs['placeholder'] = ' '
            field.required = False

    def clean_email(self):
        email = self.cleaned_data.get('email')
        # 如果用户输入了邮箱，且该邮箱已存在于数据库 (不区分大小写)
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError("このメールアドレスは既に使用されています。")
        return email

    def clean(self):
        return self.cleaned_data

# ==========================================
# 2. 用户个人资料编辑表单 (Profile - User model)
# ==========================================

# accounts/forms.py

from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()

# accounts/forms.py

from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()

class ProfileEditForm(forms.ModelForm):
    allow_30_min = forms.BooleanField(required=False, label="30分コース")
    allow_60_min = forms.BooleanField(required=False, label="60分コース")
    allow_120_min = forms.BooleanField(required=False, label="120分コース")

    class Meta:
        model = User
        # [重要] ここに表示したい項目をすべて書きます
        # username は除外し、vrc_id を追加しました
        fields = ('discord_id', 'vrc_id', 'email', 'twitter_id', 'avatar')
        
        labels = {
            'vrc_id': 'VRCHAT ID',       # 画面上の表示名
            'discord_id': 'DISCORD ID',
            'email': 'EMAIL ADDRESS',
            'twitter_id': 'X (TWITTER) ID',
        }
        
        widgets = {
            # Discord ID: 読み取り専用
            'discord_id': forms.TextInput(attrs={
                'readonly': 'readonly', 
                'class': 'cursor-not-allowed opacity-60 bg-white/5' 
            }),
            
            # VRCID: 必須入力
            'vrc_id': forms.TextInput(attrs={
                'required': 'required',  # HTML側でも必須にする
                'placeholder': 'VRChat上の名前を入力してください'
            }),

            # Email: 任意入力（Discordからは取得しないので空欄になります）
            'email': forms.EmailInput(attrs={
                'placeholder': 'contact@example.com (任意)'
            }),
            
            # Twitter: 任意
            'twitter_id': forms.TextInput(attrs={
                'placeholder': '@username'
            }),

            'avatar': forms.FileInput(attrs={
                'class': 'block w-full text-xs text-gray-400 file:mr-4 file:py-2 file:px-4 file:border-0 file:text-xs file:bg-white/10 file:text-veludo-accent-gold hover:file:bg-white/20 cursor-pointer',
                'accept': 'image/*'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # キャスト設定の読み込み（変更なし）
        if hasattr(self.instance, 'cast_profile'):
            self.fields['allow_30_min'].initial = self.instance.cast_profile.allow_30_min
            self.fields['allow_60_min'].initial = self.instance.cast_profile.allow_60_min
            self.fields['allow_120_min'].initial = self.instance.cast_profile.allow_120_min
        
        # デザイン適用
        for name, field in self.fields.items():
            if name != 'avatar':
                existing_classes = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"input-field {existing_classes}"
                if 'disabled' in field.widget.attrs:
                    del field.widget.attrs['disabled']
        
        self.fields['avatar'].widget.attrs.update({'class': 'text-gray-400 text-xs'})

    # [重要] VRCIDが空でないかサーバー側でもチェック
    def clean_vrc_id(self):
        vrc_id = self.cleaned_data.get('vrc_id')
        if not vrc_id:
            raise forms.ValidationError("VRCIDは必須です。")
        return vrc_id

    def save(self, commit=True):
        user = super().save(commit=commit)
        # キャスト設定の保存（変更なし）
        if hasattr(user, 'cast_profile'):
            profile = user.cast_profile
            profile.allow_30_min = self.cleaned_data['allow_30_min']
            profile.allow_60_min = self.cleaned_data['allow_60_min']
            profile.allow_120_min = self.cleaned_data['allow_120_min']
            profile.save()
        return user

# ==========================================
# 3. 管理员面板专用表单
# ==========================================

class UserRoleForm(forms.Form):
    user_id = forms.IntegerField(widget=forms.HiddenInput())
    is_cast = forms.BooleanField(required=False, label="Cast Permission",
        widget=forms.CheckboxInput(attrs={'class': 'w-4 h-4 text-veludo-gold bg-gray-700 border-gray-600 rounded focus:ring-veludo-gold focus:ring-2'}))
    is_staff = forms.BooleanField(required=False, label="Admin Permission",
        widget=forms.CheckboxInput(attrs={'class': 'w-4 h-4 text-red-500 bg-gray-700 border-gray-600 rounded focus:ring-red-500 focus:ring-2'}))

class CastCMSForm(forms.ModelForm):
    class Meta:
        model = CastProfile
        fields = ['name', 'intro', 'is_active', 'avatar', 'youtube_url'] 

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs['class'] = 'input-field'
            if field_name == 'intro': field.widget.attrs['rows'] = 5
            if field_name == 'is_active':
                field.widget.attrs['class'] = 'w-4 h-4 text-veludo-gold bg-gray-700 border-gray-600 rounded focus:ring-veludo-gold focus:ring-2'

# ==========================================
# 4. Cast 资料编辑表单 (修复重点)
# ==========================================

class CastProfileForm(forms.ModelForm):
    class Meta:
        model = CastProfile
        # [关键] 显式列出需要在编辑页显示的字段
        fields = ['name', 'intro', 'tags', 'youtube_url', 'avatar', 'display_order', 'is_active']
        
        # 定义样式
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full bg-black/30 border border-white/10 text-white px-4 py-2 focus:border-veludo-gold outline-none transition-colors'
            }),
            'intro': forms.Textarea(attrs={
                'class': 'w-full bg-black/30 border border-white/10 text-white px-4 py-2 h-32 focus:border-veludo-gold outline-none transition-colors'
            }),
            'tags': forms.TextInput(attrs={
                'class': 'w-full bg-black/30 border border-white/10 text-white px-4 py-2 focus:border-veludo-gold outline-none transition-colors',
                'placeholder': '["Tag1", "Tag2"]'
            }),
            'youtube_url': forms.URLInput(attrs={
                'class': 'w-full bg-black/30 border border-white/10 text-white px-4 py-2 focus:border-veludo-gold outline-none transition-colors placeholder-gray-600',
                'placeholder': 'https://youtu.be/...'
            }),
            'display_order': forms.NumberInput(attrs={
                'class': 'w-full bg-black/30 border border-white/10 text-white px-4 py-2 focus:border-veludo-gold outline-none transition-colors'
            }),
            'saas_resource_id': forms.TextInput(attrs={
                'class': 'w-full bg-black/30 border border-white/10 text-white px-4 py-2 focus:border-veludo-gold outline-none transition-colors'
            }),
            'avatar': forms.FileInput(attrs={'class': 'text-xs text-gray-400'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'w-4 h-4 text-veludo-gold bg-gray-700 border-gray-600 rounded'})
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # [关键修复] 将所有非核心字段设置为 required=False
        # 这样即使前端留空，后端也不会报错 "This field is required"
        optional_fields = ['display_order', 'intro', 'youtube_url', 'tags', 'saas_resource_id', 'avatar']
        for field in optional_fields:
            if field in self.fields:
                self.fields[field].required = False

    def clean_display_order(self):
        """
        特殊处理排序字段：如果用户留空，默认保存为 0，防止 IntegerField 报错
        """
        data = self.cleaned_data.get('display_order')
        if data is None or data == "":
            return 0
        return data

# ==========================================
# 5. 图片集 Formset
# ==========================================

CastMediaFormSet = inlineformset_factory(
    CastProfile, 
    CastMedia,
    fields=['image_file', 'order'],
    extra=1,
    can_delete=True,
    widgets={
        'image_file': forms.FileInput(attrs={'class': 'text-xs text-gray-400'}),
        'order': forms.NumberInput(attrs={'class': 'bg-black/30 border border-white/10 text-white px-2 py-1 w-full text-center'}),
    }
)