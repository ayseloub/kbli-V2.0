"""Kasus sel rusak nyata dari dataset_final.xlsx."""
import pytest

from pipeline.build_dataset import _fix_cell


@pytest.mark.parametrize("raw,uraian,notes", [
    # kata "Seluruh" tertempel membungkus uraian (74901, 47593, 47731)
    ("SelKelompok ini mencakup kegiatan penerjemahan.uruh", "Kelompok ini mencakup kegiatan penerjemahan.", []),
    ("SeluruKelompok ini mencakup usaha jasa pemakaman.h", "Kelompok ini mencakup usaha jasa pemakaman.", []),
    ("SeKelompok ini mencakup usaha perdagangan eceran.luruh", "Kelompok ini mencakup usaha perdagangan eceran.", []),
    # escape CR Excel + tanda kutip
    ('"Kode subklasifikasi : IG004_x000D_\nKelompok ini mencakup survei hidrografi"',
     "Kode subklasifikasi: IG004\nKelompok ini mencakup survei hidrografi", []),
    # catatan ruang lingkup dipindah ke scope
    ("Kelompok ini mencakup industri kimia. Catatan: industri merkuri dilarang.",
     "Kelompok ini mencakup industri kimia.", ["industri merkuri dilarang"]),
    ("Kelompok ini mencakup produksi film. (kecuali animasi)",
     "Kelompok ini mencakup produksi film.", ["Seluruh kecuali animasi"]),
    # sel yang hanya berisi batasan
    ("Selain Penjualan Langsung; Penjualan Langsung", "", ["Selain Penjualan Langsung; Penjualan Langsung"]),
    ("Lembaga Audit SMK3", "", ["Lembaga Audit SMK3"]),
])
def test_fix_cell(raw, uraian, notes):
    assert _fix_cell(raw) == (uraian, notes)
