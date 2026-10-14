"""Commit only durable state; preserve concurrent human changes without force push."""
import subprocess

def git(*args,check=True):
    return subprocess.run(['git',*args],check=check,text=True,capture_output=True)

def main():
    git('config','user.name','github-actions[bot]')
    git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    git('add','--','state')
    if git('diff','--cached','--quiet',check=False).returncode==0:return
    git('commit','-m','Save story review and render progress [skip ci]')
    git('fetch','origin','main')
    try:git('rebase','origin/main')
    except subprocess.CalledProcessError:
        git('rebase','--abort',check=False)
        raise RuntimeError('State conflicts with a newer edit. Recover state from this run artifact; no force push was attempted.') from None
    try:git('push','origin','HEAD:main')
    except subprocess.CalledProcessError:
        raise RuntimeError('State push rejected. Download the recovery artifact; no force push was attempted.') from None

if __name__=='__main__':main()
