"""Server-side PDF M-ticket generation using reportlab (optional dependency).

Kept isolated so the app still works without reportlab installed: the PDF
endpoint and email attachments degrade gracefully when the library is missing.
All reportlab imports are lazy so importing this module is always safe.
"""
from io import BytesIO

from movies.qr import ticket_qr_png_bytes


def _txt(value):
    return str(value if value is not None else '')


def build_ticket_pdf(context):
    """Return PDF bytes for a ticket context dict, or None on any failure."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
    except ImportError:
        return None

    try:
        page_w, page_h = landscape(A4)
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=(page_w, page_h))
        c.setTitle('BookMySeat Ticket - {}'.format(_txt(context.get('movie_name'))))

        red = HexColor('#e11d48')
        dark = HexColor('#111111')
        grey = HexColor('#888888')

        # Top strip
        c.setFillColor(red)
        c.rect(0, page_h - 20 * mm, page_w, 20 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 22)
        c.drawString(15 * mm, page_h - 13 * mm, 'BookMySeat')
        c.setFont('Helvetica', 11)
        c.drawRightString(page_w - 15 * mm, page_h - 13 * mm, 'M-TICKET')

        left = 15 * mm
        y = page_h - 35 * mm

        c.setFillColor(dark)
        c.setFont('Helvetica-Bold', 20)
        c.drawString(left, y, _txt(context.get('movie_name'))[:60])
        y -= 9 * mm

        def row(label, value):
            nonlocal y
            if y < 26 * mm:
                y = page_h - 30 * mm
            c.setFillColor(grey)
            c.setFont('Helvetica-Bold', 8)
            c.drawString(left, y, label.upper())
            c.setFillColor(dark)
            c.setFont('Helvetica', 12)
            c.drawString(left, y - 5 * mm, _txt(value)[:90])
            y -= 14 * mm

        row('Cinema', context.get('theatre_name'))
        row('Screen', context.get('screen_name') or 'Main')
        show_time = context.get('show_time')
        row('Showtime', show_time.strftime('%I:%M %p, %A, %d %b %Y') if show_time else '')
        row('Seats', ', '.join(context.get('seats') or []))
        row('Booking Ref', context.get('booking_ref'))
        row('Amount Paid', 'INR {}'.format(context.get('total')))
        row('Payment', '{} {}'.format(
            _txt(context.get('payment_method') or 'Online'),
            _txt(context.get('transaction_id')),
        ).strip())

        # QR (right side)
        qr_payload = context.get('qr_payload')
        if qr_payload:
            png = None
            try:
                png = ticket_qr_png_bytes(qr_payload)
            except Exception:
                png = None
            if png:
                qr_size = 48 * mm
                c.drawImage(
                    ImageReader(BytesIO(png)),
                    page_w - qr_size - 15 * mm,
                    page_h - qr_size - 30 * mm,
                    width=qr_size,
                    height=qr_size,
                    preserveAspectRatio=True,
                )
                c.setFillColor(grey)
                c.setFont('Helvetica', 8)
                c.drawCentredString(
                    page_w - qr_size / 2 - 15 * mm,
                    page_h - qr_size - 30 * mm - 4 * mm,
                    'Scan to verify at the venue',
                )

        # Footer
        c.setStrokeColor(red)
        c.setLineWidth(1)
        c.line(15 * mm, 12 * mm, page_w - 15 * mm, 12 * mm)
        c.setFillColor(grey)
        c.setFont('Helvetica', 8)
        c.drawString(15 * mm, 7 * mm, 'E-ticket - present this QR at the venue')
        c.drawRightString(page_w - 15 * mm, 7 * mm, 'BookMySeat')

        c.showPage()
        c.save()
        return buf.getvalue()
    except Exception:
        return None
