# Font for the OCR structure bridge

`NotoSans-Regular.ttf` is an unmodified Noto Sans font from
[notofonts/noto-fonts](https://github.com/notofonts/noto-fonts/blob/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf),
distributed under the SIL Open Font License in [OFL.txt](OFL.txt).

SHA-256: `b85c38ecea8a7cfb39c24e395a4007474fa5a4fc864f6ee33309eb4948d232d5`.

The GPU OCR structure bridge embeds this font to retain Vietnamese text in
headless Linux/Kaggle workers. No system font installation is required.
`OCR_BRIDGE_FONT` can select another TrueType font with Vietnamese coverage;
V6.3 records its path/hash in the immutable runtime inputs and checks it before OCR.
