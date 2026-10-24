"""Trusted controller; the actual worker runs in a separate offline leaf sandbox."""
from bench import app_trial
refuse=app_trial.refuse
def run(spec,workdir):
    return app_trial.run(spec,workdir,'omnigent')
