#```python
import os, re, csv, io
import tempfile
from typing import List, Dict, Tuple, Optional
import streamlit as st

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import Frame, Paragraph, KeepInFrame
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.utils import ImageReader
from PIL import Image

# ----------------------------
# Réglages
# ----------------------------
OUTPUT_PDF = "cartes_recto_verso.pdf"
NB_CARTES = 10
COLS, ROWS = 2, 5            # 2 x 5 = 10 cartes
MARGIN = 1.0 * cm
GAP = 0.35 * cm              # espace entre cartes (découpe)
BORDER_WIDTH = 1
ELEMENT_SPACING = 0.8 * cm   # Espace entre les éléments (texte, image) et les bords de la carte

# Couleurs (verso) selon le nom du fichier
COLOR_MAP = {
    "bleu": colors.HexColor("#2D6CDF"),
    "rouge": colors.HexColor("#D64541"),
    "rose": colors.HexColor("#E85D9E"),
    "vert": colors.HexColor("#2ECC71"),
    "jaune": colors.HexColor("#F1C40F"),
}

def pick_color_from_filename(filename: str) -> Tuple[str, colors.Color]:
    low = filename.lower()
    for key in ["bleu", "rouge", "rose", "vert", "jaune"]:
        if key in low:
            return key, COLOR_MAP[key]
    return "bleu", COLOR_MAP["bleu"]

def is_dark(c: colors.Color) -> bool:
    r, g, b = c.red, c.green, c.blue
    lum = 0.2126*r + 0.7152*g + 0.0722*b
    return lum < 0.55

def sniff_dialect(data: str) -> csv.Dialect:
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(data[:4096], delimiters=";,|, ,\t,")
    except Exception:
        dialect = csv.get_dialect("excel")
    return dialect

def normalize_header(h: str) -> str:
    return re.sub(r"\s+", "", (h or "").strip().lower())

def read_cards_from_csv(csv_file_content: str) -> List[Dict[str, str]]:
    """
    CSV attendu (souple) :
    - question : colonne 'question' (ou 1re colonne si pas d'en-tête)
    - texte verso : colonne 'texte' / 'reponse' / 'réponse' / 'answer' (ou 2e/3e colonne selon présence d'en-tête)
    """
    # Use io.StringIO to treat the string content as a file
    f = io.StringIO(csv_file_content)

    dialect = sniff_dialect(csv_file_content)
    reader = csv.reader(f, dialect)
    rows = list(reader)
    if not rows:
        return []

    first = rows[0]
    norm_first = [normalize_header(x) for x in first]
    has_header = any(x in ("question","q","texte","text","reponse","réponse","answer","reponseverso","verso") for x in norm_first)

    def get_field(d: Dict[str,str], keys: List[str], fallback: str="") -> str:
        for k in keys:
            nk = normalize_header(k)
            for kk, vv in d.items():
                if normalize_header(kk) == nk:
                    return (vv or "").strip()
        return fallback

    out = []
    if has_header:
        headers = norm_first
        for r in rows[1:]:
            if not any(str(x).strip() for x in r):
                continue
            d = {headers[i]: (r[i].strip() if i < len(r) else "") for i in range(len(headers))}
            q_raw = get_field(d, ["question","q"])
            card_color_key = None
            question_text = q_raw # Default to raw question

            # Regex to find (color) at the end of the string, case-insensitive
            match = re.search(r'\(([^)]+)\)\s*$', q_raw, re.IGNORECASE)
            if match:
                extracted_color_name = match.group(1).lower().strip()
                if extracted_color_name in COLOR_MAP:
                    card_color_key = extracted_color_name
                    question_text = re.sub(r'\s*\(([^)]+)\)\s*$', '', q_raw, flags=re.IGNORECASE).strip()

            txt = get_field(d, ["texte","text","reponse","réponse","answer","verso","reponseverso"])
            out.append({"question": question_text, "texte": txt, "card_color_key": card_color_key})
    else:
        # Sans en-tête : col1=question, col2=texte (si col2 vide, on tente col3)
        for r in rows:
            if not any(str(x).strip() for x in r):
                continue
            q_raw = (r[0].strip() if len(r) > 0 else "")
            card_color_key = None
            question_text = q_raw

            match = re.search(r'\(([^)]+)\)\s*$', q_raw, re.IGNORECASE)
            if match:
                extracted_color_name = match.group(1).lower().strip()
                if extracted_color_name in COLOR_MAP:
                    card_color_key = extracted_color_name
                    question_text = re.sub(r'\s*\(([^)]+)\)\s*$', '', q_raw, flags=re.IGNORECASE).strip()

            txt = (r[1].strip() if len(r) > 1 else "")
            if not txt and len(r) > 2:
                txt = r[2].strip()
            out.append({"question": question_text, "texte": txt, "card_color_key": card_color_key})

    return out

# ----------------------------
# Mise en page
# ----------------------------
class Grid:
    def __init__(self, page_w, page_h, card_w, card_h, x0, y0):
        self.page_w = page_w
        self.page_h = page_h
        self.card_w = card_w
        self.card_h = card_h
        self.x0 = x0
        self.y0 = y0

def compute_grid() -> Grid:
    page_w, page_h = A4
    usable_w = page_w - 2*MARGIN - (COLS-1)*GAP
    usable_h = page_h - 2*MARGIN - (ROWS-1)*GAP
    card_w = usable_w / COLS
    card_h = usable_h / ROWS
    return Grid(page_w, page_h, card_w, card_h, MARGIN, MARGIN)

def card_xy(grid: Grid, col: int, row: int) -> Tuple[float,float]:
    # row 0 en haut
    x = grid.x0 + col*(grid.card_w + GAP)
    y_top = grid.page_h - grid.y0 - row*(grid.card_h + GAP)
    y = y_top - grid.card_h
    return x, y

def draw_card_border(c: canvas.Canvas, x: float, y: float, w: float, h: float, stroke_color=colors.lightgrey):
    c.setLineWidth(BORDER_WIDTH)
    c.setStrokeColor(stroke_color)
    c.rect(x, y, w, h, stroke=1, fill=0)

def draw_centered_text_in_box(c: canvas.Canvas, x: float, y: float, w: float, h: float, text: str, style: ParagraphStyle):
    pad = 6 # Internal padding for the text within the card

    # Calculate the inner dimensions for the text area
    inner_x = x + pad
    inner_y = y + pad
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad

    p = Paragraph((text or "").replace("\n","<br/>") if (text or "").strip() else "&nbsp;", style)

    # Get the actual height the paragraph would take if wrapped within inner_w
    # We pass a temporary canvas and a very large height to allow it to compute its natural height
    text_width, text_height = p.wrapOn(c, inner_w, inner_h * 100)

    # Ensure text_height does not exceed inner_h, and shrink if necessary
    if text_height > inner_h:
        text_height = inner_h

    # Calculate vertical offset to center the text
    y_offset = (inner_h - text_height) / 2

    # Draw the paragraph
    # The y-coordinate for drawOn is the bottom-left corner of the paragraph.
    # We want to place the bottom of the paragraph at (inner_y + y_offset).
    p.drawOn(c, inner_x, inner_y + y_offset)

def build_pdf(cards: List[Dict[str,str]], default_back_color: colors.Color, output_buffer: io.BytesIO, uploaded_image_file: Optional[io.BytesIO] = None):
    grid = compute_grid()

    base_font = "Helvetica"
    style_verso = ParagraphStyle(
        "Verso", fontName=base_font, fontSize=12.5, leading=14.5,
        alignment=TA_CENTER, textColor=colors.black
    )

    cards10 = (cards[:NB_CARTES] + [{"question":"","texte":""}] * NB_CARTES)[:NB_CARTES]

    c = canvas.Canvas(output_buffer, pagesize=A4)

    original_pil_image = None
    temp_image_files_to_clean = []

    if uploaded_image_file:
        try:
            original_pil_image = Image.open(uploaded_image_file)
            if original_pil_image.mode != 'RGBA':
                original_pil_image = original_pil_image.convert('RGBA')
        except Exception as e:
            st.error(f"Erreur lors du prétraitement de l'image : {e}")
            original_pil_image = None

    # -------- Recto --------
    for i in range(NB_CARTES):
        row = i // COLS
        col = i % COLS
        x, y = card_xy(grid, col, row)

        card_specific_color_key = cards10[i].get("card_color_key")
        current_back_color = COLOR_MAP.get(card_specific_color_key, default_back_color)

        style_recto = ParagraphStyle(
            "Recto", fontName=base_font, fontSize=16, leading=18,
            alignment=TA_CENTER, textColor=(colors.white if is_dark(current_back_color) else colors.black)
        )

        c.setFillColor(current_back_color)
        c.rect(x, y, grid.card_w, grid.card_h, stroke=0, fill=1)

        image_to_draw_path = None
        if original_pil_image:
            try:
                r = current_back_color.red
                g = current_back_color.green
                b = current_back_color.blue
                bg_color_tuple = (int(r * 255), int(g * 255), int(b * 255))

                alpha_composite_img = Image.new('RGB', original_pil_image.size, bg_color_tuple)
                alpha_composite_img.paste(original_pil_image, (0, 0), original_pil_image)

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_png_file:
                    image_to_draw_path = temp_png_file.name
                    alpha_composite_img.save(temp_png_file, format='PNG')
                temp_image_files_to_clean.append(image_to_draw_path)

            except Exception as e:
                st.error(f"Erreur lors du compositing de l'image pour la carte {i}: {e}")
                image_to_draw_path = None


        if image_to_draw_path:
            img_h = grid.card_h / 2
            img_w = img_h

            img_x = x + (grid.card_w - img_w) / 2
            img_y = y + ELEMENT_SPACING

            text_box_h = grid.card_h - (3 * ELEMENT_SPACING + img_h)

            text_box_x = x
            text_box_y = img_y + img_h + ELEMENT_SPACING
            text_box_w = grid.card_w

            try:
                c.drawImage(image_to_draw_path, img_x, img_y,
                            width=img_w, height=img_h, preserveAspectRatio=True)
            except Exception as e:
                st.error(f"Erreur lors du dessin de l'image (après prétraitement) : {e}")
                draw_centered_text_in_box(c, x, y, grid.card_w, grid.card_h, cards10[i].get("question", ""), style_recto)
                continue

            draw_centered_text_in_box(c, text_box_x, text_box_y, text_box_w, text_box_h, cards10[i].get("question", ""), style_recto)
        else:
            draw_centered_text_in_box(c, x, y, grid.card_w, grid.card_h, cards10[i].get("question", ""), style_recto)

    c.showPage()

    # -------- Verso (colonnes inversées) --------
    for i in range(NB_CARTES):
        row = i // COLS
        col = i % COLS
        back_col = (COLS - 1 - col)
        x, y = card_xy(grid, back_col, row)

        draw_centered_text_in_box(c, x, y, grid.card_w, grid.card_h, cards10[i].get("texte", ""), style_verso)

    c.save()

    for temp_file in temp_image_files_to_clean:
        try:
            os.remove(temp_file)
        except OSError as e:
            st.warning(f"Erreur lors de la suppression du fichier temporaire {temp_file}: {e}")


# ----------------------------
# Streamlit Application Logic
# ----------------------------
st.title("Générateur de Cartes")

st.write("Uploadez votre fichier CSV et une image PNG avec transparence (facultatif) pour générer des cartes recto/verso.")
st.write("Le contenu du fichier CSV est constituée de lignes  ma question1 (couleur) ; ma réponse1")
st.text("                                                     ma question2 (couleur) ; ma réponse2  où couleur est la couleur")
st.write("du recto de la carte - choix possibles : bleu, rouge, rose, vert, jaune. ")
st.write("Si aucune couleur n'est indiquée : maquestion1 ; maréponse1 alors la couleur par défaut est le bleu.")

# CSV Upload
uploaded_csv_file = st.file_uploader("Uploader le fichier CSV", type=["csv"])

if uploaded_csv_file is None:
    st.warning("Veuillez uploader un fichier CSV pour commencer.")
else:
    # Read CSV content from the uploaded file
    csv_content = uploaded_csv_file.getvalue().decode("utf-8")
    csv_name = uploaded_csv_file.name

    color_name, default_back_color = pick_color_from_filename(csv_name)
    st.info(f"Couleur par défaut détectée (via nom de fichier) : {color_name}")

    cards = read_cards_from_csv(csv_content)
    st.info(f"Lignes lues : {len(cards)} (on utilise les {NB_CARTES} premières)")

    # Image Upload (optional)
    uploaded_image_file = st.file_uploader("Uploader une image PNG (facultatif) pour le recto", type=["png", "jpg", "jpeg"])

    if uploaded_image_file is None:
        st.info("Pas d'image fournie. Le recto sera uniquement textuel.")

    if st.button("Générer le PDF"):
        if cards:
            output_buffer = io.BytesIO()
            build_pdf(cards, default_back_color, output_buffer, uploaded_image_file)

            st.success(f"PDF généré : {OUTPUT_PDF}")
            st.download_button(
                label="Télécharger le PDF",
                data=output_buffer.getvalue(),
                file_name=OUTPUT_PDF,
                mime="application/pdf"
            )
        else:
            st.error("Aucune carte n'a pu être lue depuis le fichier CSV. La génération du PDF est annulée.")






