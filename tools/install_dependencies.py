"""
RecapAI - Otomatik Bagimlillik Kurulum Araci
Calistir: python tools/install_dependencies.py
"""
import subprocess
import sys
import platform
from pathlib import Path


def run_cmd(cmd, check=True):
    """Komut calistir ve sonucu goster."""
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr and result.returncode != 0:
        print(f"HATA: {result.stderr}")
    if check and result.returncode != 0:
        return False
    return True


def check_python_version():
    """Python versiyonunu kontrol et."""
    version = sys.version_info
    print(f"Python: {version.major}.{version.minor}.{version.micro}")
    if version.major != 3 or version.minor < 10 or version.minor > 12:
        print("UYARI: Python 3.10, 3.11 veya 3.12 onerilir.")
        print("Python 3.13 ile Torch henuz uyumsuz olabilir.")
        return False
    return True


def detect_cuda():
    """CUDA var mi kontrol et."""
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True
        )
        if result.returncode == 0:
            print("NVIDIA GPU tespit edildi.")
            return True
    except FileNotFoundError:
        pass
    print("NVIDIA GPU yok veya driver kurulu degil. CPU surumu kurulacak.")
    return False


def uninstall_old_torch():
    """Eski torch kurulumlarini temizle."""
    print("\n=== Eski Torch temizleniyor ===")
    packages = ["torch", "torchvision", "torchaudio"]
    for pkg in packages:
        run_cmd([sys.executable, "-m", "pip", "uninstall", "-y", pkg], check=False)


def install_torch(use_cuda=False):
    """Torch'u dogru versiyonla kur."""
    print("\n=== Torch kuruluyor ===")
    if use_cuda:
        cmd = [
            sys.executable, "-m", "pip", "install",
            "torch==2.3.1", "torchaudio==2.3.1",
            "--index-url", "https://download.pytorch.org/whl/cu121"
        ]
    else:
        cmd = [
            sys.executable, "-m", "pip", "install",
            "torch==2.3.1", "torchaudio==2.3.1",
            "--index-url", "https://download.pytorch.org/whl/cpu"
        ]
    return run_cmd(cmd)


def install_numpy():
    """NumPy uyumlu versiyon."""
    print("\n=== NumPy kuruluyor ===")
    return run_cmd([
        sys.executable, "-m", "pip", "install",
        "numpy>=1.24.0,<2.0.0", "--upgrade"
    ])


def install_main_deps():
    """Ana bagimliliklar."""
    print("\n=== Ana paketler kuruluyor ===")
    req = Path("requirements.txt")
    if not req.exists():
        print("requirements.txt bulunamadi, atlaniyor.")
        return True
    return run_cmd([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def install_kokoro():
    """Kokoro TTS."""
    print("\n=== Kokoro TTS kuruluyor ===")
    return run_cmd([
        sys.executable, "-m", "pip", "install",
        "kokoro>=0.7.16", "soundfile"
    ])


def test_torch():
    """Torch calisiyor mu test et."""
    print("\n=== Torch test ===")
    try:
        import importlib
        torch = importlib.import_module("torch")
        print(f"Torch versiyonu: {torch.__version__}")
        t = torch.tensor([1.0, 2.0, 3.0])
        print(f"Test tensor: {t}")
        print(f"CUDA mevcut: {torch.cuda.is_available()}")
        return True
    except Exception as e:
        print(f"Torch HATALI: {e}")
        return False


def test_kokoro():
    """Kokoro calisiyor mu test et."""
    print("\n=== Kokoro test ===")
    try:
        import importlib
        importlib.import_module("kokoro")
        print("Kokoro import OK")
        return True
    except Exception as e:
        print(f"Kokoro HATALI: {e}")
        return False


def check_vcredist():
    """Visual C++ Redistributable kontrolu (Windows)."""
    if platform.system() != "Windows":
        return True
    print("\n=== Visual C++ Redistributable kontrolu ===")
    print("Eger 'DLL load failed' hatasi alırsaniz, sunu indirin:")
    print("https://aka.ms/vs/17/release/vc_redist.x64.exe")
    return True


def main():
    print("=" * 60)
    print("RecapAI - Bagimlillik Kurulum Araci")
    print("=" * 60)

    if not check_python_version():
        choice = input("\nDevam etmek istiyor musunuz? (e/h): ")
        if choice.lower() != "e":
            return

    use_cuda = detect_cuda()

    print("\n=== Pip guncelleniyor ===")
    run_cmd([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

    uninstall_old_torch()
    install_numpy()
    install_torch(use_cuda)
    install_main_deps()
    install_kokoro()
    check_vcredist()

    print("\n" + "=" * 60)
    print("TEST SONUCLARI")
    print("=" * 60)
    torch_ok = test_torch()
    kokoro_ok = test_kokoro()

    print("\n" + "=" * 60)
    if torch_ok and kokoro_ok:
        print("BASARILI! RecapAI'yi baslatin: python main.py")
    else:
        print("BAZI PAKETLER YUKLENEMEDI.")
        if not torch_ok:
            print("\n1. Visual C++ Redistributable kurun:")
            print("   https://aka.ms/vs/17/release/vc_redist.x64.exe")
            print("2. Python 3.11 veya 3.12 kullanin (3.13 henuz desteklenmiyor)")
        if not kokoro_ok and torch_ok:
            print("\n3. espeak-ng kurun:")
            print("   https://github.com/espeak-ng/espeak-ng/releases")


if __name__ == "__main__":
    main()