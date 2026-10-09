"""Y1-Y9: the Python environment of an installation: interpreter, packages, requirements, conflicts, disk use,
package installs and import validation through the installation's own venv and run-as user, and dev tools.

Facts come from the filesystem (pyvenv.cfg, dist-info folders, requirement files); nothing is imported into the
app. Anything that runs Python runs the venv's interpreter as the run-as user, through its agent.
"""
