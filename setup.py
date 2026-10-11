"""Bundle runtime defaults without duplicating their maintained checkout files."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        output = Path(self.build_lib) / 'toki_finance_daily' / 'resources'
        files = {
            'config/sources.json': 'sources.json',
            'skills/toki-finance-daily/references/editorial.md': 'editorial.md',
            'examples/dataset.json': 'examples/dataset.json',
            'examples/brief.json': 'examples/brief.json',
        }
        for source, target in files.items():
            destination = output / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / source, destination)


setup(cmdclass={'build_py': BuildWithResources})
