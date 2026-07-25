import random

from flask import Blueprint, render_template, request, redirect, url_for

from models import db, Ad
from admin import admin_required

ads_bp = Blueprint("ads", __name__)

AD_PLACEMENTS = [
    {"value": "homepage_banner", "label": "Homepage (below the download card)"},
]
AD_PLACEMENT_VALUES = {p["value"] for p in AD_PLACEMENTS}


def get_active_ad(placement):
    candidates = Ad.query.filter_by(placement=placement, is_active=True).all()
    if not candidates:
        return None
    ad = random.choice(candidates)
    ad.impression_count += 1
    db.session.commit()
    return ad


@ads_bp.route("/ads/<int:ad_id>/click")
def ad_click(ad_id):
    ad = Ad.query.get_or_404(ad_id)
    ad.click_count += 1
    db.session.commit()
    return redirect(ad.link_url)


@ads_bp.route("/admin/ads")
@admin_required
def manage_ads():
    ads = Ad.query.order_by(Ad.created_at.desc()).all()
    return render_template("admin_ads.html", ads=ads, placements=AD_PLACEMENTS)


@ads_bp.route("/admin/ads", methods=["POST"])
@admin_required
def create_ad():
    placement = request.form.get("placement", "").strip()
    ad_type = request.form.get("ad_type", "image").strip()
    if ad_type not in ("image", "script"):
        ad_type = "image"

    if placement not in AD_PLACEMENT_VALUES:
        return redirect(url_for("ads.manage_ads"))

    if ad_type == "script":
        script_content = request.form.get("script_content", "").strip()
        if not script_content:
            return redirect(url_for("ads.manage_ads"))
        ad = Ad(
            placement=placement,
            ad_type="script",
            script_content=script_content,
        )
    else:
        image_url = request.form.get("image_url", "").strip()
        link_url = request.form.get("link_url", "").strip()
        alt_text = request.form.get("alt_text", "").strip()
        if not image_url or not link_url:
            return redirect(url_for("ads.manage_ads"))
        ad = Ad(
            placement=placement,
            ad_type="image",
            image_url=image_url,
            link_url=link_url,
            alt_text=alt_text or None,
        )

    db.session.add(ad)
    db.session.commit()
    return redirect(url_for("ads.manage_ads"))


@ads_bp.route("/admin/ads/<int:ad_id>/toggle", methods=["POST"])
@admin_required
def toggle_ad(ad_id):
    ad = Ad.query.get_or_404(ad_id)
    ad.is_active = not ad.is_active
    db.session.commit()
    return redirect(url_for("ads.manage_ads"))


@ads_bp.route("/admin/ads/<int:ad_id>/delete", methods=["POST"])
@admin_required
def delete_ad(ad_id):
    ad = Ad.query.get_or_404(ad_id)
    db.session.delete(ad)
    db.session.commit()
    return redirect(url_for("ads.manage_ads"))
