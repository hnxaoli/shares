from pythonforandroid.recipe import Recipe
from pythonforandroid.logger import shprint
from pythonforandroid.util import current_directory
import sh
import os

class LibffiRecipe(Recipe):
    """Use latest libffi 7.4.2 (pre-autoreconfed, no LT_SYS_SYMBOL_USCORE issue)"""
    version = '7.4.2'
    url = 'https://github.com/libffi/libffi/releases/download/v{version}/libffi-{version}.tar.gz'
    depends = []
    patches = []
    
    def build_arch(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        with current_directory(build_dir):
            env = self.get_recipe_env(arch)
            # configure + make + install (standard autotools)
            shprint(sh.Command('./configure'),
                    '--host={}'.format(arch.command_prefix),
                    '--prefix={}'.format(self.ctx.get_libjni_dir(arch)),
                    '--disable-shared',
                    '--enable-static',
                    _env=env)
            shprint(sh.make, '-j4', _env=env)
            shprint(sh.make, 'install', _env=env)

recipe = LibffiRecipe()
