"""Onde estão os arquivos do servidor: no repositório ou instalado por pacote.

O pacote (.deb/.rpm, packaging/) instala o servidor em /usr/share/pspstream
(a mesma estrutura do repositório: server/, assets/) e o auxiliar da
captura KMS em /usr/libexec/pspstream.

O auxiliar KMS só lê a tela com a permissão cap_sys_admin no arquivo (setcap).
Ela some quando o arquivo é trocado (make no repositório; nos pacotes, o
preinst/%pre a guarda e o postinst/%post a devolve).
"""
import os
import struct
from pathlib import Path
from i18n import tr

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


CAP_SYS_ADMIN = 21
VFS_CAP_FLAGS_EFFECTIVE = 0x1


def has_cap_sys_admin(path) -> bool:
    """O arquivo tem cap_sys_admin+ep? Lê o atributo security.capability (o
    mesmo do getcap), sem depender do getcap instalado."""
    try:
        data = os.getxattr(path, "security.capability")
    except OSError:
        return False
    if len(data) < 8:
        return False
    magic, permitted = struct.unpack_from("<II", data)
    return bool(magic & VFS_CAP_FLAGS_EFFECTIVE and permitted & (1 << CAP_SYS_ADMIN))


def kms_fix(path) -> str:
    """O comando que dá ao auxiliar KMS a permissão de ler a tela."""
    path = Path(path)
    if path == REPO_KMS_HELPER:
        return f"make -C {path.parent} cap"
    return f"sudo setcap cap_sys_admin+ep {path}"


def no_new_privs() -> bool:
    """Este processo roda com no_new_privs (Flatpak, alguns containers)? Aí o
    kernel ignora a permissão do arquivo nos programas que ele abre."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            return any(line.split() == ["NoNewPrivs:", "1"] for line in f)
    except OSError:
        return False


def kms_cap_ignored(path) -> str:
    """Por que a permissão do auxiliar, mesmo dada, não valeria aqui ("" se valeria)."""
    try:
        nosuid = os.statvfs(path).f_flag & os.ST_NOSUID
    except OSError:
        nosuid = False
    if nosuid:
        return tr("{path} is on a partition mounted with nosuid, which ignores the permission: use the package "
                  "or a clone of the repository on another partition").format(path=path)
    if no_new_privs():
        return tr("the server runs with no_new_privs (in a Flatpak terminal, like VS Code's, or in a "
                  "container), and the kernel ignores the helper's permission: run the server in a regular terminal")
    return ""


def kms_permission_problem(path) -> str:
    """O que fazer quando o auxiliar KMS diz que não pode ler a tela."""
    path = Path(path)
    if not has_cap_sys_admin(path):
        if path == REPO_KMS_HELPER:
            return tr("the helper {path} lacks the permission: run {fix} (again after every make)").format(
                path=path, fix=kms_fix(path))
        return tr("the helper {path} lacks the permission: run {fix}").format(path=path, fix=kms_fix(path))
    return kms_cap_ignored(path) or tr("the helper {path} has the permission, but the kernel did not return the "
                                       "image (in a container or toolbox, it does not apply outside of it)").format(path=path)
