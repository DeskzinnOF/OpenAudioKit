# OpenAudioKit em português

## Instalação e início rápido

Na raiz do projeto, com Python 3.10 ou superior em um ambiente virtual:

```sh
python -m pip install -e ".[cli,gui,api]"
python examples/generate_tone.py demo.wav
openaudiokit analyze demo.wav
openaudiokit-gui
```

Instale apenas os extras das interfaces que pretende usar. A biblioteca central usa NumPy, SciPy e SoundFile.

## Usuário

Use a análise para medir níveis, dinâmica, espectro e relação estéreo. Compare duas versões com `openaudiokit compare A.wav B.wav`. Os deltas são B menos A. Para automação, aplique uma cadeia JSON com `openaudiokit process A.wav saida.wav --preset examples/cleanup.json`. Consulte os parâmetros e exemplos no [tutorial da CLI](tutorial.md) e no [guia desktop](desktop.md).

## Integração e desenvolvimento

Use `load_audio` para ler áudio e `openaudiokit.qc.inspect` para aplicar limites de entrega. A [referência Python](python-api.md) documenta os formatos e unidades. A [API REST](rest.md) recebe bytes de áudio e tem limites por arquivo e por processo. Instale `.[cli,gui,api,docs,dev]` para rodar pytest, Ruff, mypy e MkDocs. A [arquitetura](architecture.md) descreve as fronteiras das interfaces.

## Limitações e diagnóstico

- LUFS pode ficar indisponível em clipes curtos, abaixo do gate ou fora das configurações de canais e taxas que o medidor aceita.
- Instalações Linux da GUI exigem um plug-in Qt funcional. Rode os testes gráficos com `QT_QPA_PLATFORM=offscreen` em ambientes sem monitor.
- Se a leitura falhar, verifique o codec da instalação do libsndfile, a integridade do arquivo e o limite de amostras da interface.
- Use WAV em ponto flutuante para manter picos acima da escala nominal durante a análise. Consulte as [definições de medição](measurement.md).

O [guia de publicação](releasing.md) documenta a preparação do GitHub, GitHub Pages e PyPI.
