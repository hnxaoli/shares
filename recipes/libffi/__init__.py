"""Custom libffi recipe — skips autoreconf (breaks on Ubuntu 24.04 autoconf 2.72).

p4a's built-in libffi recipe (v3.4.2) forces `autoreconf -vif` even when a
pre-generated `configure` script is present.  On Ubuntu 24.04 with autoconf
2.72 + libtool 2.4.7 this fails with:

    configure.ac:215: error: possibly undefined macro: LT_SYS_SYMBOL_USCORE

libffi 3.4.2 ships a pre-generated `configure` script.  We use it directly
and skip both autogen.sh AND autoreconf.
"""
from os.path import exists, join
from multiprocessing import cpu_count
from pythonforandroid.recipe import Recipe
from pythonforandroid.logger import shprint
from pythonforandroid.util import current_directory
import sh


class LibffiRecipe(Recipe):
    name = 'libffi'          # MUST match p4a's recipe name to override
    version = 'v3.4.2'
    url = 'https://github.com/libffi/libffi/archive/{version}.tar.gz'
    patches = ['remove-version-info.patch']  # reuse p4a's existing patch
    built_libraries = {'libffi.so': '.libs'}

    def build_arch(self, arch):
        env = self.get_recipe_env(arch)
        with current_directory(self.get_build_dir(arch.arch)):
            # libffi 3.4.2 tarball ALREADY has a pre-generated configure.
            # Skip autogen.sh AND autoreconf — Ubuntu 24.04 autoconf 2.72
            # does not understand LT_SYS_SYMBOL_USCORE from old libtool.m4.
            configure = './configure'
            if not exists(configure):
                raise RuntimeError(
                    'configure script missing in libffi source tree; '
                    'expected pre-generated configure in v3.4.2 tarball'
                )
            shprint(sh.Command(configure),
                    '--host=' + arch.command_prefix,
                    '--prefix=' + self.get_build_dir(arch.arch),
                    '--disable-builddir',
                    '--enable-shared', _env=env)
            shprint(sh.make, '-j', str(cpu_count()), 'libffi.la', _env=env)

    def get_include_dirs(self, arch):
        return [join(self.get_build_dir(arch), 'include')]


recipe = LibffiRecipe()
