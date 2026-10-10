set -eu
export DEBIAN_FRONTEND=noninteractive
export HOME=/root
export PIP_DISABLE_PIP_VERSION_CHECK=1
apt-get update -qq
apt-get install -y -qq --no-install-recommends git ripgrep ca-certificates > /dev/null
pip install --quiet --no-cache-dir --root-user-action=ignore -r /workspace/repo/requirements.txt
pip install --quiet --no-cache-dir --root-user-action=ignore /opt/doubleblind/wheels/*.whl
git config --global user.email agent@doubleblind.local
git config --global user.name "DoubleBlind agent"
git config --global init.defaultBranch main
cd /workspace/repo
git init -q
git add -A
git commit -q -m "initial state"
python --version
