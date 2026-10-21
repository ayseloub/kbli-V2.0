"""Bikin PDF regulasi tiruan untuk test (struktur mirip PP: header berulang, nomor halaman, BAB/Pasal)."""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BODY = [
    "BAB I", "KETENTUAN UMUM",
    "Pasal 1",
    "Dalam Peraturan Pemerintah ini yang dimaksud dengan:",
    "1. Perizinan Berusaha adalah legalitas yang diberikan kepada Pelaku Usaha untuk",
    "memulai dan menjalankan usaha dan/atau kegiatannya.",
    "2. Nomor Induk Berusaha yang selanjutnya disingkat NIB adalah bukti registrasi",
    "Pelaku Usaha untuk melakukan kegiatan usaha dan sebagai identitas bagi Pelaku",
    "Usaha dalam pelaksanaan kegiatan usahanya.",
    "BAB II", "PERIZINAN BERUSAHA BERBASIS RISIKO",
    "Bagian Kesatu", "Umum",
    "Pasal 7",
    "(1) Perizinan Berusaha Berbasis Risiko dilakukan berdasarkan penetapan tingkat",
    "risiko dan peringkat skala usaha kegiatan usaha.",
    "(2) Tingkat risiko sebagaimana dimaksud pada ayat (1) terdiri atas kegiatan usaha",
    "dengan tingkat risiko rendah, menengah rendah, menengah tinggi, dan tinggi.",
    "Pasal 12",
    "(1) Perizinan Berusaha untuk kegiatan usaha dengan tingkat risiko rendah",
    "sebagaimana dimaksud dalam Pasal 7 ayat (2) huruf a berupa NIB.",
    "(2) NIB sebagaimana dimaksud pada ayat (1) merupakan identitas Pelaku Usaha",
    "sekaligus legalitas untuk melaksanakan kegiatan usaha.",
    "Pasal 13",
    "Perizinan Berusaha untuk kegiatan usaha dengan tingkat risiko menengah rendah",
    "berupa NIB dan Sertifikat Standar yang merupakan pernyataan Pelaku Usaha untuk",
    "memenuhi standar usaha dalam rangka melakukan kegiatan usaha.",
    "BAB III", "SANKSI ADMINISTRATIF",
    "Pasal 30",
    "Pelaku Usaha yang tidak memenuhi kewajiban dikenai sanksi administratif berupa",
    "peringatan, penghentian sementara kegiatan usaha, pengenaan denda administratif,",
    "dan/atau pencabutan Perizinan Berusaha.",
]


def make_pdf(path: Path, lines_per_page: int = 12):
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    pages = [BODY[i:i + lines_per_page] for i in range(0, len(BODY), lines_per_page)]
    for n, chunk in enumerate(pages, 1):
        c.setFont("Helvetica", 9)
        c.drawCentredString(w / 2, h - 40, "PRESIDEN REPUBLIK INDONESIA")
        c.drawCentredString(w / 2, h - 55, f"- {n} -")
        c.setFont("Helvetica", 11)
        y = h - 90
        for ln in chunk:
            c.drawString(60, y, ln)
            y -= 18
        c.setFont("Helvetica", 8)
        c.drawString(60, 40, "SK No 123456 A")
        c.showPage()
    c.save()


if __name__ == "__main__":
    out = Path(__file__).parent / "fixtures" / "pp_tiruan.pdf"
    make_pdf(out)
    print(out)
