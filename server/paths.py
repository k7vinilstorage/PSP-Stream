"""Onde estão os arquivos do servidor: no repositório ou instalado por pacote.

O pacote (.deb/.rpm, packaging/) instala o servidor em /usr/share/pspstream
(a mesma estrutura do repositório: server/, assets/) e o auxiliar da
captura KMS em /usr/libexec/pspstream.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_KMS_HELPER = ROOT / "tools" / "kms" / "pspstream-kms"
SYSTEM_KMS_HELPERS = (Path("/usr/libexec/pspstream/pspstream-kms"), Path("/usr/lib/pspstream/pspstream-kms"))


def installed() -> bool:
    """Rodando do pacote (sem a pasta tools/ do repositório)?"""
    return not (ROOT / "tools").is_dir()


def kms_helper() -> Path:
    """O auxiliar KMS: PSPSTREAM_KMS_HELPER, o compilado no repositório ou o
    do pacote. Se nenhum existe, o do repositório (a mensagem de erro diz
    para compilar)."""
    env = os.environ.get("PSPSTREAM_KMS_HELPER")
    if env:
        return Path(env)
    for path in (REPO_KMS_HELPER, *SYSTEM_KMS_HELPERS):
        if path.exists():
            return path
    return REPO_KMS_HELPER
