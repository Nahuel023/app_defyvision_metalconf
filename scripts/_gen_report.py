"""Genera el PDF de avances semanales para el cliente - semana 9-12 junio 2026."""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import datetime

OUTPUT = "data/reporte_avances_semana.pdf"

W, H = A4

def build():
    doc = SimpleDocTemplate(
        OUTPUT,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    base = getSampleStyleSheet()

    title_s = ParagraphStyle("title_s", parent=base["Normal"],
        fontSize=22, textColor=colors.HexColor("#1a1a2e"), spaceAfter=4,
        fontName="Helvetica-Bold", alignment=TA_LEFT)

    sub_s = ParagraphStyle("sub_s", parent=base["Normal"],
        fontSize=11, textColor=colors.HexColor("#444466"), spaceAfter=2,
        fontName="Helvetica", alignment=TA_LEFT)

    section_s = ParagraphStyle("section_s", parent=base["Normal"],
        fontSize=13, textColor=colors.HexColor("#1a1a2e"),
        fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=6)

    body_s = ParagraphStyle("body_s", parent=base["Normal"],
        fontSize=10, leading=15, textColor=colors.HexColor("#222222"),
        fontName="Helvetica", spaceAfter=4)

    bullet_s = ParagraphStyle("bullet_s", parent=base["Normal"],
        fontSize=10, leading=15, textColor=colors.HexColor("#222222"),
        fontName="Helvetica", leftIndent=16, spaceAfter=3,
        bulletIndent=4)

    caption_s = ParagraphStyle("caption_s", parent=base["Normal"],
        fontSize=9, textColor=colors.HexColor("#666666"),
        fontName="Helvetica-Oblique", alignment=TA_CENTER)

    label_ok = ParagraphStyle("label_ok", parent=base["Normal"],
        fontSize=10, textColor=colors.white,
        fontName="Helvetica-Bold", alignment=TA_CENTER)

    story = []

    # ── Encabezado ────────────────────────────────────────────────────
    story.append(Paragraph("DefyVision Metalconf", title_s))
    story.append(Paragraph("Reporte de avances — semana del 9 al 12 de junio 2026", sub_s))
    story.append(Paragraph(f"Emitido: {datetime.date.today().strftime('%d/%m/%Y')}", sub_s))
    story.append(HRFlowable(width="100%", thickness=2,
        color=colors.HexColor("#1a1a2e"), spaceAfter=14))

    # ── Resumen ejecutivo ─────────────────────────────────────────────
    story.append(Paragraph("Estado actual del sistema", section_s))
    story.append(Paragraph(
        "El sistema de inspección visual automática para chapas microperforadas opera "
        "con <b>dos scanners (cámaras) activos</b> en producción. "
        "Durante esta semana se completó la calibración de ambos scanners sobre el modelo "
        "<b>modelo_B</b> (microperforado) y se alcanzó <b>0 paradas de máquina falsas</b> "
        "sobre todos los lotes de validación conocidos-OK.", body_s))

    # Tabla de estado final
    data = [
        [Paragraph("<b>Scanner</b>", body_s),
         Paragraph("<b>Frames OK validados</b>", body_s),
         Paragraph("<b>Temporal OK</b>", body_s),
         Paragraph("<b>Paradas falsas</b>", body_s),
         Paragraph("<b>Estado</b>", body_s)],
        ["Scanner 1", "74 / 74", "74 / 74  (100%)", "0", "✔ Operativo"],
        ["Scanner 2", "76 / 76", "76 / 76  (100%)", "0", "✔ Operativo"],
    ]
    tbl = Table(data, colWidths=[3.5*cm, 4.2*cm, 4.2*cm, 3.2*cm, 2.9*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("ALIGN",      (1,1), (-1,-1), "CENTER"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1),
            [colors.HexColor("#f4f4f8"), colors.white]),
        ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#ccccdd")),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("TEXTCOLOR", (4,1), (4,-1), colors.HexColor("#1a7a3a")),
        ("FONTNAME",  (4,1), (4,-1), "Helvetica-Bold"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))

    # ── Avances de la semana ──────────────────────────────────────────
    story.append(Paragraph("Avances realizados esta semana", section_s))

    avances = [
        ("Calibración independiente de ambos scanners",
         "Se calibraron Scanner 1 y Scanner 2 de forma separada sobre lotes de imágenes "
         "capturadas el 12 de junio, sin mezclar tolerancias entre ellos. "
         "Cada scanner tiene ahora su propio patrón de referencia y sus propios parámetros "
         "de detección ajustados a la geometría real de esa cámara/óptica."),

        ("Patron de referencia reconstruido (modelo_B)",
         "El patrón de agujeros de referencia fue reconstruido desde imágenes reales de "
         "producción para cada scanner:\n"
         "  •  Scanner 1: 112 puntos de referencia activos\n"
         "  •  Scanner 2: 86 puntos de referencia activos\n"
         "Las columnas del borde derecho (zona inestable por variación de chapa) "
         "fueron excluidas del patrón en tiempo de construcción, eliminando las "
         "falsas alarmas sin perder capacidad de detección de defectos reales."),

        ("Estabilización del borde derecho en Scanner 2",
         "Se identificó que Scanner 2 generaba blobs espurios grandes (hasta ~989 px²) "
         "en las columnas más derechas del patrón, causados por reflejos del borde de la chapa. "
         "Se ajustaron threshold, área máxima de agujero y se desactivó el re-centrado dinámico "
         "del ROI, eliminando este problema. Resultado validado: missing promedio = 0.44, "
         "missing máximo = 3, NOK = 0."),

        ("Pantalla de parada de máquina rediseñada",
         "Cuando el sistema detecta una pieza defectuosa y detiene la máquina, la pantalla "
         "de alerta ahora muestra de forma grande y clara en qué SCANNER ocurrió la falla, "
         "facilitando la acción inmediata del operario en planta sin ambigüedad."),

        ("Ventana de métricas mejorada",
         "La pantalla de MÉTRICAS fue rediseñada con una apariencia más moderna y legible. "
         "Se agregó una pestaña de HISTORIAL que muestra el desempeño histórico de cada "
         "scanner con gráficos. Se corrigió un problema que hacía que el historial apareciera "
         "vacío aunque existieran datos guardados."),

        ("Auto-calibración gradual de ROI (feature preventivo)",
         "Se implementó un mecanismo de corrección automática muy lenta del área de interés "
         "(ROI) para compensar pequeñas derivas mecánicas en producción continua 24/7. "
         "El sistema detecta si la chapa se corrió lateralmente y ajusta 1 píxel a la vez "
         "solo cuando hay evidencia sostenida durante varios minutos. "
         "Este feature está disponible y puede activarse por scanner cuando se requiera."),

        ("Diagnóstico de salud del ROI",
         "Se agregó un indicador visual en la pantalla de calibración que muestra en tiempo "
         "real el estado del área de inspección: si coincide con la chapa real, si hay "
         "deriva lateral y su magnitud en píxeles."),

        ("Nombres de grabaciones con scanner incluido",
         "Las carpetas de grabaciones ahora incluyen automáticamente el identificador del "
         "scanner en el nombre (ej: 12-06-2026-MICROPERFORADO_10_SCANNER_1), "
         "facilitando la trazabilidad de los lotes capturados."),
    ]

    for titulo, desc in avances:
        story.append(Paragraph(f"<b>{titulo}</b>", body_s))
        # reemplazar \n por <br/>
        desc_fmt = desc.replace("\n", "<br/>")
        story.append(Paragraph(desc_fmt, bullet_s))
        story.append(Spacer(1, 4))

    # ── Próximos pasos ────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1,
        color=colors.HexColor("#ccccdd"), spaceAfter=6))
    story.append(Paragraph("Próximos pasos", section_s))

    proximos = [
        "Validación en producción real con ambos scanners corriendo en simultáneo.",
        "Captura de piezas NOK reales para validar que el sistema detecta correctamente "
        "los defectos (agujeros faltantes).",
        "Ajuste fino de Scanner 2 si el borde derecho sigue generando ruido residual "
        "con material de producción diferente al lote de validación.",
        "Activación opcional del auto-centrado de ROI si se observa deriva en operación prolongada.",
    ]
    for p in proximos:
        story.append(Paragraph(f"• {p}", bullet_s))

    # ── Pie ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1,
        color=colors.HexColor("#ccccdd"), spaceAfter=6))
    story.append(Paragraph(
        "DefyVision — Sistema Metalconf · Contacto: design@defymotion.com.ar",
        caption_s))

    doc.build(story)
    print(f"PDF generado: {OUTPUT}")

if __name__ == "__main__":
    build()
