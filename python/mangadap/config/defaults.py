# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-
r"""
Provides a set of functions that define and return defaults used by the
MaNGA DAP, such as paths and file names.

*License*:
    Copyright (c) 2015, SDSS-IV/MaNGA Pipeline Group
        Licensed under BSD 3-clause license - see LICENSE.rst

*Source location*:
    $MANGADAP_DIR/python/mangadap/config/defaults.py

*Imports and python version compliance*:
    ::

        from __future__ import division
        from __future__ import print_function
        from __future__ import absolute_import
        from __future__ import unicode_literals
        
        import sys
        if sys.version > '3':
            long = int
    
        import os.path
        from os import environ
        import glob
        import numpy
        from mangadap.util.exception_tools import check_environment_variable

*Revision history*:
    | **23 Apr 2015**: Original implementation by K. Westfall (KBW)
    | **19 May 2015**: (KBW) Documentation and Sphinx tests
    | **20 May 2015**: (KBW) TODO: Change the default names of the par
        and output fits files
    | **15 Jun 2015**: (KBW) Added default functions moved from
        :class:`mangadap.drpfile`
    | **16 Jun 2015**: (KBW) Added wavelength limits and lower flux
        limit to
        :func:`mangadap.config.inputdata.available_template_libraries`
    | **17 Jun 2015**: (KBW) Moved declarations of template library keys
        to its own function: *default_template_library_keys*, and
        edited :func:`available_template_libraries` accordingly
    | **27 Aug 2015**: (KBW) Changed the name of the plan file; added
        :func:`default_dap_file_root` based on file_root() from
        :class:`mangadap.survey.rundap`.
    | **28 Aug 2015**: (KBW) Added :func:`default_manga_fits_root` and
        :func:`default_cube_covariance_file`
    | **07 Oct 2015**: (KBW) Adjusted for changes to naming of the
        template library database definitions.  Added M11-STELIB-ZSOL
        library.
    | **29 Jan 2016**: (KBW) Changed
        :func:`manga.config.inputdata.available_template_libraries` to
        use configparser ini files to define each template library.
    | **03 Feb 2016**: (KBW) Added checks for required environmental
        variables.
    | **17 Feb 2016**: (KBW) Added try/except blocks for importing
        ConfigParser.
    | **16 Mar 2016**: (KBW) Created :mod:`mangadap.config.inputdata`
        and moved **default_template_libraries** there (and changed it
        to
        :func:`mangadap.config.inputdata.available_template_libraries`.
        No longer need ConfigParser here.

"""

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import
from __future__ import unicode_literals

import sys
if sys.version > '3':
    long = int

import os.path
from os import environ
import glob
import numpy
from mangadap.util.exception_tools import check_environment_variable

__author__ = 'Kyle B. Westfall'


def default_idlutils_dir():
    """
    Return the default IDLUTILS directory.

    """
    check_environment_variable('IDLUTILS_DIR')
    return environ['IDLUTILS_DIR']


def default_drp_version():
    """
    Return the DRP version defined by the environmental variable
    MANGADRP_VER.

    """
    check_environment_variable('MANGADRP_VER')
    return environ['MANGADRP_VER']


def default_redux_path(drpver=None):
    """
    Return the main output path for the DRP products using the
    environmental variable MANGA_SPECTRO_REDUX.

    Args:
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.

    Returns:
        str: Path to reduction directory
    """
    # Make sure the DRP version is set
    if drpver is None:
        drpver = default_drp_version()
    check_environment_variable('MANGA_SPECTRO_REDUX')
    return os.path.join(environ['MANGA_SPECTRO_REDUX'], drpver)


def default_drp_directory_path(plate, drpver=None, redux_path=None):
    """
    Return the exact directory path with the DRP file.

    Args:
        plate (int): Plate number
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.
        redux_path (str): (**Optional**) Path to the root reduction
            directory.  Default is to use :func:`default_redux_path`.

    Returns:
        str: Path to the directory with the 3D products of the DRP
    """
    # Make sure the redux path is set
    if redux_path is None:
        redux_path = default_redux_path(drpver=drpver)

    return os.path.join(redux_path, str(plate), 'stack')


# TODO: Are these values kept in MANGACORE somewhere?
def default_cube_pixelscale():
    """
    Return the default pixel scale of the DRP CUBE files in arcsec.
    """
    return 0.5


def default_cube_width_buffer():
    """
    Return the default width buffer in pixels used when regridding the
    DRP RSS spectra into the CUBE format.
    """
    return 10


def default_cube_recenter():
    """
    Return the default recentering flag used when regridding the DRP RSS
    spectra into the CUBE format.
    """
    return False


def default_regrid_rlim():
    """
    Return the default limiting radius for the Gaussian kernel used when
    regridding the DRP RSS spectra into the CUBE format.
    """
    return 1.6


def default_regrid_sigma():
    """
    Return the default standard deviation of the Gaussian kernel used
    when regridding the DRP RSS spectra into the CUBE format.
    """
    return 0.7


def default_cube_covariance_file(plate, ifudesign):
    """
    Return the default name for the covariance calculated for a 'CUBE'
    fits file.

    Args:
        plate (int): Plate number
        ifudesign (int): IFU design number
    
    Returns:
        str: The default name of the covariance file.
    """
    root = default_manga_fits_root(plate, ifudesign, 'CUBE')
    return ('{0}_COVAR.fits'.format(root))


def default_dap_source():
    """
    Return the root path to the DAP source directory using the
    environmental variable MANGADAP_DIR.

    """
    check_environment_variable('MANGADAP_DIR')
    return environ['MANGADAP_DIR']


def default_dap_version():
    """
    Return the DAP version defined by the environmental variable
    MANGADAP_VER.

    """
    check_environment_variable('MANGADAP_VER')
    return environ['MANGADAP_VER']


def default_analysis_path(drpver=None, dapver=None):
    """
    Return the main output path for the DAP using the environmental
    variable MANGA_SPECTRO_ANALYSIS.

    Args:
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.
        dapver (str): (**Optional**) DAP version.  Default is to use
            :func:`default_dap_version`.

    Returns:
        str: Path to analysis directory
    """
    # Make sure the DRP version is set
    if drpver is None:
        drpver = default_drp_version()
    # Make sure the DAP version is set
    if dapver is None:
        dapver = default_dap_version()
    check_environment_variable('MANGA_SPECTRO_ANALYSIS')
    return os.path.join(environ['MANGA_SPECTRO_ANALYSIS'], drpver, dapver)


#def default_dap_directory_path(plate, ifudesign, drpver=None, dapver=None, analysis_path=None):
#    Return the exact directory path with the DAP file.
#
#    Args:
#        plate (int): Plate number
#        ifudesign (int): IFU design number
#        drpver (str): (Optional) DRP version.  Default is to use
#            :func:`default_drp_version`.
#        dapver (str): (Optional) DAP version.  Default is to use
#            :func:`default_dap_version`.
#        analysis_path (str): (Optional) Path to the root analysis
#            directory.  Default is to use :func:`default_analysis_path`
#
#    Returns:
#        str: Path to the directory with DAP output files
#
#    # Make sure the DRP version is set
#    if analysis_path is None:
#        analysis_path = default_analysis_path(drpver=drpver, dapver=dapver)
#
#    return os.path.join(analysis_path, str(plate), str(ifudesign))
def default_dap_reference_path(plate=None, drpver=None, dapver=None, analysis_path=None):
    """
    Return the path to the top-level reference path for a given plate.

    Args:
        plate (int): (**Optional**) Plate number, for reference
            directory of a specific plate.
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.
        dapver (str): (**Optional**) DAP version.  Default is to use
            :func:`default_dap_version`.
        analysis_path (str): (**Optional**) Path to the root analysis
            directory.  Default is to use :func:`default_analysis_path`

    Returns:
        str: Path to the directory with DAP output files
    """
    # Make sure the DRP version is set
    if analysis_path is None:
        analysis_path = default_analysis_path(drpver=drpver, dapver=dapver)

    return os.path.join(analysis_path, 'ref') \
                if plate is None else os.path.join(analysis_path, 'ref', str(plate))


def default_dap_method_path(method_dir, plate, drpver=None, dapver=None, analysis_path=None):
    """
    Return the path to the top-level path for specific DAP method and a
    given plate.

    Args:
        method_dir (str): Name to give subdirectory for the specific
            method used to create the data.  This is completely
            unconstrained, except that it must have non-zero length.
        plate (int): (**Optional**) Plate number, for reference
            directory of a specific plate.
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.
        dapver (str): (**Optional**) DAP version.  Default is to use
            :func:`default_dap_version`.
        analysis_path (str): (**Optional**) Path to the root analysis
            directory.  Default is to use :func:`default_analysis_path`

    Returns:
        str: Path to the directory with DAP output files
    """
    if len(method_dir) == 0:
        raise ValueError('method_dir undefined!')

    # Make sure the DRP version is set
    if analysis_path is None:
        analysis_path = default_analysis_path(drpver=drpver, dapver=dapver)

    return os.path.join(analysis_path, method_dir, str(plate))


def default_manga_fits_root(plate, ifudesign, mode):
    """
    Generate the main root name for the output MaNGA fits files for a
    given plate/ifudesign/mode.

    .. todo::
        - Include a "sampling mode" (LIN vs. LOG)?

    Args:
        plate (int): Plate number
        ifudesign (int): IFU design number
        mode (str): Mode of the DRP reduction; either RSS or CUBE

    Returns:
        str: Root name for a MaNGA fits file:
        `manga-[PLATE]-[IFUDESIGN]-LOG[MODE]`
    """
    return 'manga-{0}-{1}-LOG{2}'.format(plate, ifudesign, mode)


def default_dap_file_root(plate, ifudesign, mode):
    """
    Generate the root name of the MaNGA DAP parameter and script files
    for a given plate/ifudesign/mode.
    
    Args:
        plate (int): Plate number
        ifudesign (int): IFU design number
        mode (str): Mode of the DRP reduction; either RSS or CUBE

    Returns:
        str: Root name for the DAP file:
        `mangadap-[PLATE]-[IFUDESIGN]-LOG[MODE]`
    """
    return 'mangadap-{0}-{1}-LOG{2}'.format(plate, ifudesign, mode)


def default_dap_par_file(plate, ifudesign, mode, drpver=None, dapver=None, analysis_path=None,
                         directory_path=None):
    """
    Return the full path to the DAP par file.

    Args:
        plate (int): Plate number
        ifudesign (int): IFU design number
        mode (str): Mode of the DRP reduction; either RSS or CUBE
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.
        dapver (str): (**Optional**) DAP version.  Default is to use
            :func:`default_dap_version`.
        analysis_path (str): (**Optional**) Path to the root analysis
            directory.  Default is to use :func:`default_analysis_path`
        directory_path (str): (**Optional**) Path to the directory with
            the DAP output files.  Default is to use
            :func:`default_dap_reference_path`

    Returns:
        str: Full path to the DAP par file
    """
    # Make sure the directory path is defined
    if directory_path is None:
        directory_path = default_dap_reference_path(plate=plate, drpver=drpver, dapver=dapver,
                                                    analysis_path=analysis_path)
    # Set the name of the par file; put this in its own function?
    par_file = '{0}.par'.format(default_dap_file_root(plate, ifudesign, mode))
    return os.path.join(directory_path, par_file)

    
def default_dap_plan_file(output_mode, drpver=None, dapver=None, analysis_path=None,
                          directory_path=None):
    """
    Return the full path to the DAP plan file.

    Args:
        output_mode (str): Mode of the DAP output; see AnalysisPlan
        drpver (str): (**Optional**) DRP version.  Default is to use
            :func:`default_drp_version`.
        dapver (str): (**Optional**) DAP version.  Default is to use
            :func:`default_dap_version`.
        analysis_path (str): (**Optional**) Path to the root analysis
            directory.  Default is to use :func:`default_analysis_path`
        directory_path (str): (**Optional**) Path to the directory with
            the DAP output files.  Default is to use
            :func:`default_dap_reference_path`

    Returns:
        str: Full path to the DAP plan file
    """
    # Make sure the directory path is defined
    if directory_path is None:
        directory_path = default_dap_reference_path(drpver=drpver, dapver=dapver,
                                                    analysis_path=analysis_path)
    # Set the name of the plan file; put this in its own function?
    plan_file = 'mangadap-plan-{0}.par'.format(output_mode)
    return os.path.join(directory_path, plan_file)


def default_dap_file_name(plate, ifudesign, mode, output_mode, compressed=True):
    """
    Return the name of the DAP output fits file.

    Args:
        plate (int): Plate number
        ifudesign (int): IFU design number
        mode (str): Mode of the DRP reduction; either RSS or CUBE
        output_mode (str) : Output mode designation

    Returns:
        str: Name of the DAP output file.
    """
    # Number of spaces provided for iteration number is 3
    root = default_manga_fits_root(plate, ifudesign, mode)
    # TODO: ? Use default_dap_file_root() or change to:
#    siter = str(niter).zfill(3)
#    return 'manga-{0}-{1}-LOG{2}_{3}-{4}.fits'.format(plate, ifudesign, mode, bintype, siter)
#    return '{0}_BIN-{1}-{2}.fits'.format(root, bintype, siter)
    return ('{0}-{1}.fits.gz' if compressed else '{0}-{1}.fits').format(root, output_mode)


def default_plate_target_files():
    """
    Return the default plateTarget files in mangacore and their
    associated catalog indices.  The catalog indices are determined
    assuming the file names are of the form::

        'plateTargets-{0}.par'.format(catalog_id)

    Returns:
        numpy.array: Two arrays: the first contains the identified
        plateTargets files found using the default search string, the
        second provides the integer catalog index determined for each
        file.
    """
    # Default search string
    check_environment_variable('MANGACORE_DIR')
    search_str = os.path.join(environ['MANGACORE_DIR'], 'platedesign', 'platetargets',
                              'plateTargets*.par')
    file_list = glob.glob(search_str)                       # List of files
    nfiles = len(file_list)
    trgid = numpy.zeros(nfiles, dtype=numpy.int)            # Array to hold indices
    for i in range(nfiles):
        suffix = file_list[i].split('-')[1]                 # Strip out the '{i}.par'
        trgid[i] = int(suffix[:suffix.find('.')])           # Strip out '{i}'
    
    return numpy.array(file_list), trgid

