"""Memorial de cálculo em PDF, evoluído de ``CodigocomPDF.py``.

Mantém os títulos de seção e a redação em português do script original
("1. ESTABILIDADE GLOBAL", "2. VERIFICAÇÃO DO LIMITE DE ESBELTEZ", etc.) e a
convenção de nome de arquivo ``Memorial_Compressao_<bitola>.pdf``, com sufixos
numéricos para evitar sobrescrever relatórios existentes, mas:

- Lê os valores de :class:`~app.domain.models.DesignResult` em vez de
  variáveis soltas calculadas inline no script.
- Desenha a seção transversal cotada a partir da mesma
  :func:`~app.domain.drawing.build_section_scene` usada pela UI
  (``app/ui/section_canvas.py``), em vez do retângulo simplificado ad-hoc que
  ``CodigocomPDF.py`` desenhava com ``pdf.rect``.
- Corrige a tabela de geometria: o script original rotulava ``d_mm`` como
  ``'b'`` (resíduo do acesso posicional ``v[2]``/``v[3]``); aqui a tabela usa
  os nomes de campo reais do perfil (``bf``, ``tw``, ``tf``, ``h``, ``d``,
  ``d'``).
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from fpdf import FPDF

from app.domain.calculation_explanation import (
    EXPLANATION_NOTE,
    CalculationStep,
    build_calculation_steps,
)
from app.domain.drawing import Dimension, Polyline, SectionScene, build_section_scene
from app.domain.models import DesignResult, LoadCase, Profile, ProfileFamily, SteelType

_NORMA_BASE = "ABNT NBR 8800:2024"
_AUTOR_PADRAO = "Autor(a) Responsável"
_FONTE_SIMBOLOS = "ArialSimbolos"
_CAMINHO_FONTE_SIMBOLOS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
_CAMINHO_FONTE_SIMBOLOS_NEGRITO = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"

# Fontes core do fpdf2 (Helvetica/Times/Courier) só codificam latin-1. Texto
# livre digitado pelo usuário (campo "Autor") pode conter pontuação "smart"
# do autocorretor do Windows/Word (traço-en/em, aspas curvas, reticências) ou
# outros caracteres fora desse conjunto, o que fazia `pdf.cell` levantar
# `FPDFUnicodeEncodingException` e o botão "Gerar PDF" falhar com esse erro.
_SUBSTITUICOES_LATIN1 = {
    "–": "-",  # en dash
    "—": "-",  # em dash
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",  # espaço não separável
}


def _sanitizar_latin1(texto: str) -> str:
    """Normaliza ``texto`` para o conjunto latin-1 aceito pelas fontes core do PDF."""

    for original, substituto in _SUBSTITUICOES_LATIN1.items():
        texto = texto.replace(original, substituto)
    return texto.encode("latin-1", errors="replace").decode("latin-1")


class _RelatorioPDF(FPDF):
    """Página do memorial: cabeçalho com autor/norma, rodapé com nº de página."""

    def __init__(self, autor: str) -> None:
        super().__init__(format="A4")
        self._autor = autor
        self._fonte_simbolos_disponivel = _CAMINHO_FONTE_SIMBOLOS.exists()
        if self._fonte_simbolos_disponivel:
            self.add_font(_FONTE_SIMBOLOS, "", str(_CAMINHO_FONTE_SIMBOLOS), uni=True)
            if _CAMINHO_FONTE_SIMBOLOS_NEGRITO.exists():
                self.add_font(_FONTE_SIMBOLOS, "B", str(_CAMINHO_FONTE_SIMBOLOS_NEGRITO), uni=True)

    def header(self) -> None:
        self.set_fill_color(240, 243, 246)
        self.rect(10, 10, 190, 8, style="DF")
        logo_path = Path(__file__).resolve().parents[1] / "ui" / "assets" / "structdim_logo.png"
        self.image(str(logo_path), x=11, y=10.5, w=7)
        self.set_xy(18, 10)
        self.set_font("Helvetica", "B", 11)
        self.cell(182, 8, "MEMORIAL DE CÁLCULO ESTRUTURAL - COMPRESSÃO PURA", align="C")
        self.set_xy(10, 18)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, f"Autor(a) responsável: {self._autor}", border="LR", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 5, f"Norma de referência: {_NORMA_BASE}", border="LRB", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Página {self.page_no()}", align="C")


def _add_secao(pdf: FPDF, titulo: str) -> None:
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(20, 40, 100)
    pdf.cell(0, 5, titulo, border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)


def _add_linha(pdf: FPDF, texto: str) -> None:
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, texto, new_x="LMARGIN", new_y="NEXT")


def _add_linha_rotulada(pdf: FPDF, rotulo: str, valor: str) -> None:
    fonte = _FONTE_SIMBOLOS if getattr(pdf, "_fonte_simbolos_disponivel", False) else "Helvetica"
    pdf.set_font(fonte, "", 9)
    pdf.cell(0, 5, f"{rotulo} {valor}", new_x="LMARGIN", new_y="NEXT")


def _draw_dashed_line(pdf: FPDF, x1: float, y1: float, x2: float, y2: float, dash: float, gap: float) -> None:
    """Traço tracejado manual (independe de ``FPDF.set_dash_pattern``)."""

    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 0:
        return
    ux = (x2 - x1) / length
    uy = (y2 - y1) / length
    period = dash + gap
    pos = 0.0
    while pos < length:
        fim = min(pos + dash, length)
        pdf.line(x1 + ux * pos, y1 + uy * pos, x1 + ux * fim, y1 + uy * fim)
        pos += period


def _draw_dashed_circle(pdf: FPDF, cx: float, cy: float, radius: float, dash: float, gap: float) -> None:
    """Círculo tracejado manual, aproximado por cordas curtas."""

    if radius <= 0:
        return
    dash_ang = dash / radius
    gap_ang = gap / radius
    period_ang = dash_ang + gap_ang
    if period_ang <= 0:
        return
    two_pi = 2 * math.pi
    ang = 0.0
    while ang < two_pi:
        fim = min(ang + dash_ang, two_pi)
        n = max(int((fim - ang) / 0.2), 1)
        prev_x = cx + radius * math.cos(ang)
        prev_y = cy + radius * math.sin(ang)
        for i in range(1, n + 1):
            a = ang + (fim - ang) * i / n
            x = cx + radius * math.cos(a)
            y = cy + radius * math.sin(a)
            pdf.line(prev_x, prev_y, x, y)
            prev_x, prev_y = x, y
        ang += period_ang


def _draw_section_scene(
    pdf: FPDF,
    scene: SectionScene,
    x0: float,
    y0: float,
    max_w: float,
    max_h: float,
    scale_override: float | None = None,
    align_top: bool = False,
) -> None:
    """Desenha ``scene`` (mm, origem no centro, +y para cima) na página do PDF.

    A caixa de destino é ``(x0, y0)`` até ``(x0+max_w, y0+max_h)`` em mm de
    página. A cena é ajustada (fit) mantendo proporção e centralizada nessa
    caixa; o eixo Y é invertido (PDF cresce para baixo).
    """

    scale_fit = min(max_w / scene.width_mm, max_h / scene.height_mm)
    scale = min(scale_override, scale_fit) if scale_override is not None else scale_fit
    cx = x0 + max_w / 2
    cy = y0 + scene.height_mm * scale / 2 if align_top else y0 + max_h / 2

    def to_page(point: tuple[float, float]) -> tuple[float, float]:
        return (cx + point[0] * scale, cy - point[1] * scale)

    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.3)
    for outline in scene.outline:
        if isinstance(outline, Polyline):
            pts = [to_page(p) for p in outline.points]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                pdf.line(x1, y1, x2, y2)

    pdf.set_draw_color(90, 90, 90)
    pdf.set_line_width(0.15)
    pdf.set_font("Helvetica", "", 6.5)
    for dim in scene.dimensions:
        _draw_dimension(pdf, dim, to_page, scale)

    for circle in scene.circles:
        cx_page, cy_page = to_page(circle.center)
        radius_page = circle.radius * scale
        if circle.dashed:
            _draw_dashed_circle(pdf, cx_page, cy_page, radius_page, dash=1, gap=0.8)
        else:
            pdf.ellipse(cx_page - radius_page, cy_page - radius_page, 2 * radius_page, 2 * radius_page, style="D")

def _draw_dimension(pdf: FPDF, dim: Dimension, to_page, scale: float) -> None:
    if dim.horizontal:
        y = dim.p1[1] + dim.offset
        p1_line = to_page((dim.p1[0], y))
        p2_line = to_page((dim.p2[0], y))
    else:
        x = dim.p1[0] + dim.offset
        p1_line = to_page((x, dim.p1[1]))
        p2_line = to_page((x, dim.p2[1]))

    pdf.line(p1_line[0], p1_line[1], p2_line[0], p2_line[1])
    tick_half = 1.0
    if dim.horizontal:
        pdf.line(p1_line[0], p1_line[1] - tick_half, p1_line[0], p1_line[1] + tick_half)
        pdf.line(p2_line[0], p2_line[1] - tick_half, p2_line[0], p2_line[1] + tick_half)
    else:
        pdf.line(p1_line[0] - tick_half, p1_line[1], p1_line[0] + tick_half, p1_line[1])
        pdf.line(p2_line[0] - tick_half, p2_line[1], p2_line[0] + tick_half, p2_line[1])

    t = dim.label_t
    anchor_x = p1_line[0] + (p2_line[0] - p1_line[0]) * t
    anchor_y = p1_line[1] + (p2_line[1] - p1_line[1]) * t
    text_w = pdf.get_string_width(dim.label) + 1
    # Rótulo do lado de fora da peça: offset negativo = abaixo / à esquerda.
    if dim.horizontal:
        pdf.set_xy(anchor_x - text_w / 2, anchor_y + 0.5 if dim.offset < 0 else anchor_y - 6.5)
    else:
        if dim.offset < 0:
            x_texto = anchor_x + 1 if dim.label_inside else anchor_x - text_w - 1.5
        else:
            x_texto = (
                anchor_x - text_w - 1.5
                if dim.label_inside
                else anchor_x + dim.label_offset * scale + 1
            )
        pdf.set_xy(x_texto, anchor_y - 2)
    pdf.cell(text_w, 4, dim.label, align="C")


def _add_geometria(pdf: FPDF, profile: Profile, r: DesignResult) -> None:
    _add_secao(pdf, "GEOMETRIA DA SEÇÃO TRANSVERSAL")
    y_tabela = pdf.get_y()

    pdf.set_draw_color(190, 195, 200)
    pdf.set_line_width(0.2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(30, 7, "DIMENSÃO", border="B", align="L")
    pdf.cell(40, 7, "VALOR", border="B", align="C")
    pdf.cell(85, 7, "PROPRIEDADE GEOMÉTRICA", border="B", align="L")
    pdf.cell(35, 7, "VALOR", border="B", new_x="LMARGIN", new_y="NEXT", align="R")

    linhas = [
        ("bf", f"{profile.bf_mm:.1f} mm", "Área bruta (Ag)", f"{profile.ag_cm2:.2f} cm²"),
        ("tw", f"{profile.tw_mm:.1f} mm", "Momento de inércia em X (Ix)", f"{profile.ix_cm4:.2f} cm4"),
        ("tf", f"{profile.tf_mm:.1f} mm", "Momento de inércia em Y (Iy)", f"{profile.iy_cm4:.2f} cm4"),
        ("h", f"{profile.h_mm:.1f} mm", "Raio de giração em X (rx)", f"{profile.rx_cm:.2f} cm"),
        ("d", f"{profile.d_mm:.1f} mm", "Raio de giração em Y (ry)", f"{profile.ry_cm:.2f} cm"),
        ("d'", f"{profile.d_linha_mm:.1f} mm", "Raio de giração polar (r0)", f"{r.r0_cm:.2f} cm"),
    ]
    pdf.set_font("Helvetica", "", 8)
    for dim_nome, dim_valor, prop_nome, prop_valor in linhas:
        pdf.cell(30, 7, dim_nome, border="B", align="L")
        pdf.cell(40, 7, dim_valor, border="B", align="C")
        pdf.cell(85, 7, prop_nome, border="B", align="L")
        pdf.cell(35, 7, prop_valor, border="B", new_x="LMARGIN", new_y="NEXT", align="R")

    # O perfil ocupa uma área própria abaixo da tabela, centralizado na página.
    pdf.set_y(y_tabela + 55)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(10)
    pdf.cell(190, 6, "SEÇÃO TRANSVERSAL COTADA", new_x="LMARGIN", new_y="NEXT", align="C")
    # Reserva espaço para a cota superior (tw) não invadir o título.
    y_desenho = pdf.get_y() + 4
    scene = build_section_scene(profile)
    _draw_section_scene(pdf, scene, x0=67, y0=y_desenho, max_w=76, max_h=50, align_top=True)
    pdf.set_y(max(pdf.get_y(), y_desenho + 58))
    pdf.ln(4)


def _add_dados_entrada(pdf: FPDF, profile: Profile, steel: SteelType, loads: LoadCase) -> None:
    _add_secao(pdf, "DADOS DE ENTRADA")
    familia = "Laminado" if profile.family == ProfileFamily.LAMINADO else "Soldado"
    linhas = [
        (
            "Perfil",
            f"{profile.bitola} ({familia})",
            "Esforço solicitante (Nc,Sd)",
            f"{loads.ncsd_kn:g} kN",
        ),
        (
            "Aço",
            f"{steel.designacao} (fy = {steel.fy_mpa:g} MPa)",
            "Módulo de elasticidade (Ea)",
            f"{loads.ea_gpa:g} GPa",
        ),
        (
            "Flambagem em X",
            f"Kx = {loads.kx:g}  |  Lx = {loads.lx_m:g} m",
            "Módulo transversal (Ga)",
            f"{loads.ga_gpa:g} GPa",
        ),
        (
            "Flambagem em Y",
            f"Ky = {loads.ky:g}  |  Ly = {loads.ly_m:g} m",
            "Flambagem em Z (torção)",
            f"Kz = {loads.kz:g}  |  Lz = {loads.lz_m:g} m",
        ),
    ]
    pdf.set_draw_color(190, 195, 200)
    pdf.set_line_width(0.2)
    for rotulo_a, valor_a, rotulo_b, valor_b in linhas:
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(28, 7, _sanitizar_latin1(rotulo_a), border="B")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(67, 7, _sanitizar_latin1(valor_a), border="B")
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(45, 7, _sanitizar_latin1(rotulo_b), border="B")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(50, 7, _sanitizar_latin1(valor_b), border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def _add_resultado(pdf: FPDF, r: DesignResult) -> None:
    if pdf.get_y() > pdf.h - pdf.b_margin - 45:
        pdf.add_page()
    _add_secao(pdf, "RESULTADO FINAL")
    _add_linha_rotulada(pdf, "Esforço resistente (Nc,Rd)", f": {r.nc_rd_kn:.2f} kN")
    _add_linha_rotulada(pdf, "Esforço solicitante (Nc,Sd)", f": {r.ncsd_kn:.2f} kN")
    if r.atende_resistencia:
        verificacao = ": Nc,Sd ≤ Nc,Rd - ATENDE AO DIMENSIONAMENTO"
    else:
        verificacao = ": Nc,Sd > Nc,Rd - NÃO ATENDE AO DIMENSIONAMENTO"
    _add_linha_rotulada(pdf, "Verificação", verificacao)
    _add_linha_rotulada(
        pdf,
        "Limite de esbeltez",
        f": {r.esbeltez_max:.2f} (limite {r.limite_esbeltez:.0f}) - "
        + ("ATENDE AO LIMITE DE ESBELTEZ" if r.atende_esbeltez else "NÃO ATENDE AO LIMITE DE ESBELTEZ"),
    )
    rotulo = "λ0 / χ" if getattr(pdf, "_fonte_simbolos_disponivel", False) else "lambda0 / chi"
    _add_linha_rotulada(pdf, rotulo, f": {r.lambda0:.4f} / {r.chi:.4f}")


_SUBSCRITOS_SEM_GLIFO = str.maketrans({"₀": "0", "₁": "1", "₂": "2"})


def _texto_equacao(texto: str, fonte_unicode: bool) -> str:
    texto = texto.translate(_SUBSCRITOS_SEM_GLIFO)
    return texto if fonte_unicode else _sanitizar_latin1(texto)


def _altura_estimada(equations: tuple[str, ...]) -> float:
    return 12 + sum(5 * (1 + len(e) // 100) if e else 3 for e in equations)


def _add_memoria(pdf: FPDF, steps: tuple[CalculationStep, ...]) -> None:
    pdf.add_page()
    _add_secao(pdf, "MEMÓRIA DE CÁLCULO")
    fonte_unicode = getattr(pdf, "_fonte_simbolos_disponivel", False)
    fonte_equacao = _FONTE_SIMBOLOS if fonte_unicode else "Helvetica"

    for step in steps:
        if pdf.get_y() + _altura_estimada(step.equations) > pdf.h - pdf.b_margin:
            pdf.add_page()
        pdf.ln(2)
        pdf.set_x(10)
        pdf.set_fill_color(240, 243, 246)
        pdf.set_text_color(20, 40, 100)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, _sanitizar_latin1(step.title), new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

        for equation in step.equations:
            pdf.set_x(14)
            if equation in ("Mesa", "Alma"):
                pdf.ln(2)
                pdf.set_x(14)
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(0, 6, equation, new_x="LMARGIN", new_y="NEXT")
            elif equation in ("Ocorre flambagem local", "Não ocorre flambagem local"):
                ruim = equation.startswith("Ocorre")
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(*((180, 35, 24) if ruim else (24, 121, 78)))
                pdf.cell(0, 5, _sanitizar_latin1(equation), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
            elif equation:
                pdf.set_font(fonte_equacao, "", 9)
                pdf.multi_cell(0, 5, _texto_equacao(equation, fonte_unicode))
                pdf.ln(0.5)
            else:
                pdf.ln(3)

        pdf.ln(2)


def gerar_memorial_pdf(
    profile: Profile,
    steel: SteelType,
    loads: LoadCase,
    result: DesignResult,
    autor: str | None = None,
    output_dir: str | Path = ".",
) -> Path:
    """Gera o memorial de cálculo e retorna o caminho do PDF salvo.

    Usa ``Memorial_Compressao_<bitola>.pdf`` e acrescenta um sufixo numérico
    quando o nome já existe, sem sobrescrever arquivos anteriores.
    """

    autor_sanitizado = _sanitizar_latin1(autor.strip()) if autor and autor.strip() else _AUTOR_PADRAO
    pdf = _RelatorioPDF(autor=autor_sanitizado)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    _add_dados_entrada(pdf, profile, steel, loads)
    _add_geometria(pdf, profile, result)
    _add_resultado(pdf, result)
    pdf.ln(4)
    pdf.set_x(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, _sanitizar_latin1(EXPLANATION_NOTE))
    _add_memoria(pdf, build_calculation_steps(profile, steel, loads, result))

    bitola_segura = re.sub(r'[<>:"/\\|?*]', "_", profile.bitola).strip(" .")
    nome_base = f"Memorial_Compressao_{bitola_segura}"
    conteudo = pdf.output()
    indice = 0
    while True:
        sufixo = f" ({indice})" if indice else ""
        caminho = Path(output_dir) / f"{nome_base}{sufixo}.pdf"
        try:
            arquivo = caminho.open("xb")
        except FileExistsError:
            indice += 1
            continue
        with arquivo:
            arquivo.write(conteudo)
        return caminho
