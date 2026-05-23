from .models import SiteFooterCredit


def site_footer_credit(request):
    try:
        footer_credit = SiteFooterCredit.get_solo()
    except Exception:
        footer_credit = None
    return {"site_footer_credit": footer_credit}
