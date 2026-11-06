3 BAN — DUNG CHUNG CODE, TACH CAU HINH
=======================================

1_Whisper_BanCaNhan.zip          (editions/whisper/edition.json)
   Ban CA NHAN 1 khach, 1 may. Giao dien Whisper goc (KHONG Starlight).
   Tu dang nhap tren may khach, them giong dung ngay, /admin = trang Cai dat. 37 giong Anh.

2_StarlightVoice_DoanhNghiep.zip (editions/starlight/edition.json)
   Ban DOANH NGHIEP Starlight. Dang nhap, may chu + may tram LAN, duyet giong,
   nhat ky (Audit Log), quota 3 nhan vien. 37 giong Anh.

3_StarlightVoice_BanTest.zip     (editions/starlight_test/edition.json)
   Ban TEST CHUC NANG Starlight: giong ban 2, chi 3 giong Anh (adam, brittney, ray)
   de test nhanh tren GPU. Co san model giong Viet -> tu them giong Viet de test.

LAM VIEC
--------
  * Sua code trong app/  -> ca 3 ban deu co (khong sua 3 lan).
  * Sua rieng 1 ban     -> chi sua editions/<ban>/edition.json:
        personal   : true = ban ca nhan (tu dang nhap)
        voices     : null = moi giong trong goi | ["adam", ...] = chi cac giong nay
        en_voices  : giong Anh hien trong danh sach chon | "all" = hien het
        zip        : ten file zip
        brand_from : dung chung logo/mau cua ban khac (giao dien nam o editions/<ban>/static/)
  * Chay thu tren Mac    : bam dup Run-Whisper.command / Run-Starlight.command / Run-Starlight-Test.command
  * Cap nhat goi zip     : python3 dong_goi.py whisper   (hoac starlight, starlight_test, all)
  * Dong goi tu dau      : python3 export_for_client.py --edition <ban>   (can python/ ban Windows)
