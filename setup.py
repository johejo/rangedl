from setuptools import setup, find_packages

setup(name='rangedl',
      version='1.0.0',
      license='MIT',
      description='HTTP Range Downloader',
      author='Mitsuo Heijo',
      author_email='mitsuo_h@outlook.com',
      url='http://github.com/johejo/rangedl.git',
      packages=find_packages(),
      entry_points={
          'console_scripts': 'rangedl = http_download:main'
      },
      install_requires=['tqdm>=4.15.0',
                        'requests>=2.14.2']
      )
