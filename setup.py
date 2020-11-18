try:
    from setuptools import setup, find_packages
except ImportError:
    from distutils.core import setup
import os
import io
from os.path import dirname, join

def get_version(relpath):
  '''Read version info from a file without importing it'''
 
  for line in io.open(join(dirname(__file__), relpath), encoding='cp437'):
    if '__version__' in line:
      if '"' in line:
        return line.split('"')[1]
      elif "'" in line:
        return line.split("'")[1]

long_description = 'library for parsing xbrl documents'
if os.path.exists('README.rst'):
    long_description = open('README.rst').read()

setup(
    name='python-xbrl',
    version=get_version('xbrl/__init__.py'),
    description='library for parsing xbrl documents',
    author='Joe Cabrera',
    author_email='jcabrera@eminorlabs.com',
    url='https://github.com/greedo/python-xbrl/',
    license='Apache License',
    keywords='xbrl, Financial, Accounting, file formats',
    packages=['xbrl'],
    install_requires=['pytest', 'pep8', 'marshmallow',
    'beautifulsoup4', 'lxml'],
    classifiers=[
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Office/Business :: Financial',
    ],
    long_description=long_description
)
