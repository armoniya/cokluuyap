"""
uyap_panel — UYAP Ağ Geçidi için yeniden tasarlanmış arayüz katmanı.

Tek bir UI-bağımsız çekirdek (`uyap_panel.core`) iki ön yüz tarafından paylaşılır:

  • uyap_panel.gui  — ttkbootstrap masaüstü kontrol paneli
  • uyap_panel.web  — Django yerel (localhost) kontrol paneli

Her iki ön yüz de mevcut `uyap_core` paketini (office_agent / home_client /
yerel proxy 8800 / iş kuyruğu) çekirdek üzerinden sarmalar; UYAP iş mantığı
`uyap_core` içinde kalır, burada SADECE arayüz + ince servis katmanı yaşar.
"""

__all__ = ["core"]
