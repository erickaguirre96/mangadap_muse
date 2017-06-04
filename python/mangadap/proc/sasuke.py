# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-
"""
Implements an emission-line fitting class that largely wraps pPXF.

*License*:
    Copyright (c) 2017, SDSS-IV/MaNGA Pipeline Group
        Licensed under BSD 3-clause license - see LICENSE.rst

*Source location*:
    $MANGADAP_DIR/python/mangadap/proc/sasuke.py

*Imports and python version compliance*:
    ::

        from __future__ import division
        from __future__ import print_function
        from __future__ import absolute_import
        from __future__ import unicode_literals

        import sys
        import warnings
        if sys.version > '3':
            long = int

        import time
        import os
        import logging

        import numpy
        from scipy import interpolate, fftpack
        import astropy.constants
        from astropy.modeling import FittableModel, Parameter

        from ..par.parset import ParSet
        from ..par.emissionlinedb import EmissionLineDB
        from ..util.fileio import init_record_array
        from ..util.instrument import spectrum_velocity_scale, resample_vector
        from ..util.log import log_output
        from ..util.pixelmask import SpectralPixelMask
        from .spatiallybinnedspectra import SpatiallyBinnedSpectra
        from .stellarcontinuummodel import StellarContinuumModel
        from .spectralfitting import EmissionLineFit
        from .util import residual_growth

*Class usage examples*:
        Add examples

*Revision history*:
    | **24 May 2017**: Original implementation started by K. Westfall (KBW)

.. _glob.glob: https://docs.python.org/3.4/library/glob.html
.. _scipy.optimize.least_squares: http://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html
.. _scipy.optimize.OptimizeResult: http://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.OptimizeResult.html

.. _astropy.io.fits.hdu.hdulist.HDUList: http://docs.astropy.org/en/v1.0.2/io/fits/api/hdulists.html
.. _astropy.modeling: http://docs.astropy.org/en/stable/modeling/index.html
.. _astropy.modeling.FittableModel: http://docs.astropy.org/en/stable/api/astropy.modeling.FittableModel.html
.. _astropy.modeling.polynomial.Legendre1D: http://docs.astropy.org/en/stable/api/astropy.modeling.polynomial.Legendre1D.html
.. _astropy.modeling.models.CompoundModel: http://docs.astropy.org/en/stable/modeling/compound-models.html

"""

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import
from __future__ import unicode_literals

import sys
import warnings
if sys.version > '3':
    long = int

import time
import os
import logging
import itertools

import numpy
from scipy import interpolate, fftpack

import astropy.constants
from astropy.modeling import FittableModel, Parameter
from astropy.modeling.polynomial import Legendre1D

from ..par.parset import ParSet
from ..par.emissionlinedb import EmissionLineDB
from ..contrib.ppxf import ppxf
from ..util.fileio import init_record_array
from ..util.instrument import spectrum_velocity_scale, resample_vector, spectral_coordinate_step
from ..util.instrument import SpectralResolution
from ..util.log import log_output
from ..util.pixelmask import SpectralPixelMask
from ..util.constants import DAPConstants
from ..util import lineprofiles
from .spatiallybinnedspectra import SpatiallyBinnedSpectra
from .stellarcontinuummodel import StellarContinuumModel
from .spectralfitting import EmissionLineFit
from .bandpassfilter import emission_line_equivalent_width
from .util import residual_growth
from .ppxffit import PPXFFit, PPXFFitResult

# For debugging
from matplotlib import pyplot

class EmissionLineTemplates:
    r"""
    Construct a set of emission-line templates based on an emission-line
    database.

    Args:
        wave (array-like): A single wavelength vector with the
            wavelengths for the template spectra.
        sigma_inst (float,array-like): The single value or value as a
            function of wavelength for the instrumental dispersion to
            use for the template construction.
        log (bool): (**Optional**) Flag that the wavelengths have been
            sampled geometrically.
        emldb (:class:`mangadap.par.emissionlinedb.EmissionLineDB'): (**Optional**)
            Emission-line database that is parsed to setup which lines
            to include in the same template because they are modeled as
            having the same velocity, velocity dispersion and flux
            ratio.  If not provided, no templates are constructed in
            instantiation; to build the templates using an existing
            instantiation, use :func:`build_templates`.

    """
    def __init__(self, wave, sigma_inst, log=True, emldb=None, loggers=None, quiet=False):

        self.loggers=None
        self.quiet=None

        self.wave = numpy.asarray(wave)
        if len(self.wave.shape) != 1:
            raise ValueError('Provided wavelengths must be a single vector.')

        _sinst = numpy.full(self.wave.size, sigma_inst, dtype=float) \
                    if isinstance(sigma_inst, (int, float)) else numpy.asarray(sigma_inst)
        if _sinst.shape != self.wave.shape:
            raise ValueError('Provided sigma_inst must be a single number or a vector with the'
                             'same length as the wavelength vector.')
        self.sigma_inst = interpolate.interp1d(self.wave, _sinst, assume_sorted=True)

        self.dv = numpy.full(self.wave.size, spectrum_velocity_scale(wave), dtype=float) if log \
                    else astropy.constants.c.to('km/s').value*spectral_coordinate_step(wave)/wave

        self.emldb = None           # Original database
        self.ntpl = None            # Number of templates
        self.flux = None            # Template fluxes
        self.tpli = None            # Template associated with each emission line
        self.comp = None            # Kinematic component associated with each template
        self.vgrp = None            # Velocity group associated with each template
        self.sgrp = None            # Sigma group associated with each template
        self.eml_sigma_inst = None  # Instrumental dispersion at the center of each line

        if emldb is not None:
            self.build_templates(emldb, loggers=loggers, quiet=quiet)


    def _tied_index(self, i, primary=False):
        """
        Return the index of the line to which this one is tied and it's
        primary line, which can be the same.
        """
        db_rows = numpy.arange(self.emldb.neml)
        indx = db_rows[self.emldb['index'] == int(self.emldb['mode'][i][1:])][0]
        if not primary:
            return indx
        max_iter = 100
        j = 0
        while self.emldb['mode'][indx] != 'f' and j < max_iter:
            indx = db_rows[self.emldb['index'] == int(self.emldb['mode'][indx][1:])][0]
            j+=1
        if j == max_iter:
            raise ValueError('Line {0} (index={1}) does not trace back to a primary line!'.format(
                                i, self.emldb['index'][i]))
        return indx


    def _parse_emission_line_database(self):
        r"""
        Parse the input emission-line database; see
        :class:`mangadap.par.emissionlinedb.EmissionLinePar`.

        Only lines with `action=f` are included in any template.  The
        array :attr:`tpli` provides the index of the template with each
        line in the emission-line database.  Lines that are not assigned
        to any template, either because they do not have `action=f` or
        their center lies outside the wavelength range in :attr:`wave`,
        are given an index of -1.

        Only lines with `mode=a` (i.e., tie flux, velocity, and velocity
        dispersion) are included in the same template.

        Lines with tied velocities are assigned the same velocity
        component (:attr:`vcomp`) and lines with the tied velocity
        dispersions are assigned the same sigma component
        (:attr:`scomp`).

        .. warning::
            The construction of templates for use with :class:`Sasuke`
            does *not* allow one to tie fluxes while leaving the
            velocities and/or velocity dispersions as independent.

        """
        # Get the list of lines to ignore
        ignore_line = self.emldb['action'] != 'f'

        # The total number of templates to construct is the number of
        # lines in the database minus the number of lines with mode=aN
        tied_all = numpy.array([m[0] == 'a' for m in self.emldb['mode']])
        self.ntpl = self.emldb.neml - numpy.sum(ignore_line) - numpy.sum(tied_all)

        # Initialize the components
        self.comp = numpy.zeros(self.ntpl, dtype=int)-1
        self.vgrp = numpy.zeros(self.ntpl, dtype=int)-1
        self.sgrp = numpy.zeros(self.ntpl, dtype=int)-1

        # All the primary lines go into individual templates, kinematic
        # components, velocity groups, and sigma groups
        self.tpli = numpy.zeros(self.emldb.neml, dtype=int)-1
        primary_line = (self.emldb['mode'] == 'f') & numpy.invert(ignore_line)
        nprimary = numpy.sum(primary_line)
        self.tpli[primary_line] = numpy.arange(nprimary)
        self.comp[:nprimary] = numpy.arange(nprimary)
        self.vgrp[:nprimary] = numpy.arange(nprimary)
        self.sgrp[:nprimary] = numpy.arange(nprimary)

        finished = primary_line | ignore_line
        while numpy.sum(finished) != self.emldb.neml:
            # Find the indices of lines that are tied to finished lines
            for i in range(self.emldb.neml):
                if finished[i]:
                    continue
                indx = self._tied_index(i)
                if not finished[indx]:
                    continue

                finished[i] = True

                # Mode=a: Line is part of an existing template
                if self.emldb['mode'][i][0] == 'a':
                    self.tpli[i] = self.tpli[indx]
                # Mode=k: Line is part of a different template but an
                # existing kinematic component
                if self.emldb['mode'][i][0] == 'k':
                    self.tpli[i] = numpy.amax(self.tpli)+1
                    self.comp[self.tpli[i]] = self.comp[self.tpli[indx]]
                    self.vgrp[self.tpli[i]] = self.vgrp[self.tpli[indx]]
                    self.sgrp[self.tpli[i]] = self.sgrp[self.tpli[indx]]
                # Mode=v: Line is part of a different template and
                # kinematic component with an untied sigma, but tied to
                # an existing velocity group
                if self.emldb['mode'][i][0] == 'v':
                    self.tpli[i] = numpy.amax(self.tpli)+1
                    self.comp[self.tpli[i]] = numpy.amax(self.comp)+1
                    self.sgrp[self.tpli[i]] = numpy.amax(self.sgrp)+1
                    self.vgrp[self.tpli[i]] = self.vgrp[self.tpli[indx]]
                # Mode=s: Line is part of a different template and
                # kinematic component with an untied velocity, but tied
                # to an existing sigma group
                if self.emldb['mode'][i][0] == 's':
                    self.tpli[i] = numpy.amax(self.tpli)+1
                    self.comp[self.tpli[i]] = numpy.amax(self.comp)+1
                    self.vgrp[self.tpli[i]] = numpy.amax(self.vgrp)+1
                    self.sgrp[self.tpli[i]] = self.sgrp[self.tpli[indx]]

        # Debug:
        if numpy.any(self.comp < 0) or numpy.any(self.vgrp < 0) or numpy.any(self.sgrp < 0):
            raise ValueError('DEBUG: Incorrect parsing of emission-line database.')


    def check_database(self, emldb):
        r"""
        Check that the provided emission-line database can be used with
        the :class:`EmissionLineTemplates` class.  Most checks are
        performed by
        :func:`mangadap.proc.spectralfitting.EmissionLineFit.check_emission_line_database`.

        Additional checks specific to :class:`EmissionLineTemplates`
        are:
            - Any lines with mode `w` is treated as `f` and a warning is
              provided.
            - The :class:`EmissionLineTemplates` object *cannot* be used
              with mode `x`; any lines with this mode will cause a
              ValueError to be raised..

        This function does *not* check if the initial parameters
        provided by the database are consistent with other elements in
        the database because they are not used to construct the
        templates.

        Args:
            emldb (:class:`mangadap.par.emissionlinedb.EmissionLineDB'):
                Emission-line database.

        Raises:
            TypeError: Raised if the provided object is not an instance
                of :class:`mangadap.par.emissionlinedb.EmissionLineDB`.
            ValueError: Raised if any line has a mode of `x` or if the
                database does not provide a valid definition for any
                templates.
            NameError: Raised if a defined profile type is not known.
        """
        EmissionLineFit.check_emission_line_database(emldb, wave=self.wave, check_par=False)

        # Check that no lines only tie the fluxes
        if numpy.any([m[0] == 'x' for m in emldb['mode']]):
            raise ValueError('Cannot tie only fluxes in an EmissionLineTemplates object.')

        # Warn user of any lines with mode=w
        if numpy.any([m[0] == 'w' for m in emldb['mode']]):
            warnings.warn('Any line with mode=w treated the same as mode=f.')


    def build_templates(self, emldb, loggers=None, quiet=False):
        r"""
        Build the set of templates for a given emission-line database.
        The function uses the current values in :attr:`wave` and
        :attr:`sigma_inst`.

        Warn the user if any line is undersampled; i.e., the FWHM of the
        line is less than 2.1 or sigma < 0.9.

        Warn the user if any line grouped in the same template falls
        outside the spectral range.

        Args:
            emldb (:class:`mangadap.par.emissionlinedb.EmissionLineDB'):
                Emission-line database.

        Returns:
            numpy.ndarray: Returns 4 arrays: (1) the set of templates
            with shape :math:`N_{\rm tpl}\times N_{\rm wave}`, (2) the
            kinematic component assignement for each template, (3) the
            velocity group associated with each template, and (4) the
            sigma group assocated with each template.
        """
        #---------------------------------------------------------------
        # Initialize the reporting
        if loggers is not None:
            self.loggers = loggers
        self.quiet = quiet

        #---------------------------------------------------------------
        # Check the database can be used with this class
        self.check_database(emldb)
        # Save a pointer to the database
        self.emldb = emldb
        # Parse the database for construction of the templates
        self._parse_emission_line_database()

        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO,
                       'Number of emission lines to fit: {0}'.format(numpy.sum(self.tpli>-1)))
            log_output(self.loggers, 1, logging.INFO,
                       'Number of emission-line templates: {0}'.format(len(self.comp)))
            log_output(self.loggers, 1, logging.INFO,
                       'Number of emission-line kinematic components: {0}'.format(
                                                                    numpy.amax(self.comp)+1))
            log_output(self.loggers, 1, logging.INFO,
                       'Number of emission-line velocity groups: {0}'.format(
                                                                    numpy.amax(self.vgrp)+1))
            log_output(self.loggers, 1, logging.INFO,
                       'Number of emission-line sigma groups: {0}'.format(
                                                                    numpy.amax(self.sgrp)+1))

        # Get the instrumental dispersion at the center of each line
        self.eml_sigma_inst = self.sigma_inst(self.emldb['restwave'])

        # Constuct the templates
        self.flux = numpy.zeros((self.ntpl,self.wave.size), dtype=float)
        for i in range(self.ntpl):
            # Find all the lines associated with this template:
            index = numpy.arange(self.emldb.neml)[self.tpli == i]
            # Add each line to the template
            for j in index:
                profile = eval('lineprofiles.'+self.emldb['profile'][j])()
                p = profile.parameters_from_moments(self.emldb['flux'][j], 0.0,
                                                    self.eml_sigma_inst[j])
                v = astropy.constants.c.to('km/s').value*(self.wave/self.emldb['restwave'][j]-1)
                srt = numpy.argsort(numpy.absolute(v))
                if self.eml_sigma_inst[j]/self.dv[srt[0]] < 0.9:
                    warnings.warn('{0} line is undersampled!'.format(self.emldb['name'][j]))
                self.flux[i,:] += profile(v, p)

        return self.flux, self.comp, self.vgrp, self.sgrp


class SasukePar(ParSet):
    """
    A class specific to the DAP's use of Sasuke.

    Should be possible to allow for a replacement set of templates.

    """
    def __init__(self, stellar_continuum, emission_lines, guess_redshift=None,
                 guess_dispersion=None, minimum_snr=None, pixelmask=None,
                 reject_boxcar=None, bias=None, moments=None, degree=None, mdegree=None):

        arr_like = [ numpy.ndarray, list ]
        arr_in_fl = [ numpy.ndarray, list, int, float ]
        in_fl = [ int, float ]

        pars =     [ 'stellar_continuum', 'emission_lines', 'guess_redshift', 'guess_dispersion',
                     'minimum_snr', 'pixelmask', 'reject_boxcar', 'bias', 'moments', 'degree',
                     'mdegree' ]
        values =   [ stellar_continuum, emission_lines, guess_redshift, guess_dispersion,
                     minimum_snr, pixelmask, reject_boxcar, bias, moments, degree, mdegree ]
        defaults = [ None, None, None, None, 0.0, None, None, None, 2, -1, 8 ]
        dtypes =   [ StellarContinuumModel, EmissionLineDB, arr_in_fl, arr_in_fl, in_fl,
                     SpectralPixelMask, int, in_fl, int, int, int ]

        ParSet.__init__(self, pars, values=values, defaults=defaults, dtypes=dtypes)


class Sasuke(EmissionLineFit):
    """
    Use ninja skills and pPXF to fit emission lines.

    https://en.wikipedia.org/wiki/Sasuke_Uchiha
    """
    def __init__(self, bitmask, loggers=None, quiet=False):

        EmissionLineFit.__init__(self, 'sasuke', bitmask)
        # Attributes kept by SpectralFitting:
        #   fit_type='emission_line', bitmask=bitmask, par=None
        # Attributes kept by EmissionLineFit:
        #   fit_method='sasuke'

        # Logging and terminal output
        self.loggers = loggers
        self.quiet = quiet

        # Data to fit
        self.obj_wave = None
        self.obj_flux = None
        self.obj_ferr = None
        self.obj_sres = None
        self.nobj = None
        self.npix_obj = None
        self.input_obj_mask = None
        self.obj_to_fit = None
        self.input_cz = None
        self.velscale = None

        # Template data
        self.tpl_wave = None
        self.tpl_flux = None
        self.tpl_sres = None
        self.tpl_to_use = None
        self.nstpl = None
        self.ntpl = None
        self.npix_tpl = None
        self.tpl_npad = None
        self.tpl_rfft = None

        self.matched_resolution = None
        self.velscale_ratio = None

        self.emldb = None
        self.neml = None

        # Kinematic components and tied parameters
        self.ncomp = None
        self.tpl_comp = None
        self.tpl_vgrp = None
        self.tpl_sgrp = None
        self.comp_moments = None
        self.comp_start_kin = None
        self.npar_kin = None
        self.nfree_kin = None
        self.tied = None

        # Fitting parameters
        self.velocity_limits = None
        self.sigma_limits = None
        self.gh_limits = None

        self.bias = None
        self.degree = None
        self.mdegree = None
        self.reject_boxcar = None
#        self.fix_kinematics = False

        self.spectrum_start = None
        self.spectrum_end = None
        self.dof = None
        self.base_velocity = None


    @staticmethod
    def _per_fit_dtype(ntpl, nadd, nmult, nkin, mask_dtype):
        r"""
        Construct the record array data type for the output fits
        extension.
        """

        return [ ('BINID',numpy.int),
                 ('BINID_INDEX',numpy.int),
                 ('MASK', mask_dtype),
                 ('BEGPIX', numpy.int),
                 ('ENDPIX', numpy.int),
                 ('NPIXTOT',numpy.int),
                 ('NPIXFIT',numpy.int),
                 ('KINCMP',numpy.int,(ntpl,)),
                 ('VELCMP',numpy.int,(ntpl,)),
                 ('SIGCMP',numpy.int,(ntpl,)),
                 ('USETPL',numpy.bool,(ntpl,)),
                 ('TPLWGT',numpy.float,(ntpl,)),
                 ('ADDCOEF',numpy.float,(nadd,)) if nadd > 1 else ('ADDCOEF',numpy.float),
                 ('MULTCOEF',numpy.float,(nmult,)) if nmult > 1 else ('MULTCOEF',numpy.float),
                 ('KININP',numpy.float,(nkin,)),
                 ('KIN',numpy.float,(nkin,)),
                 ('KINERR',numpy.float,(nkin,)),
                 ('TIEDKIN',numpy.int,(nkin,)),
                 ('CHI2',numpy.float),
                 ('RCHI2',numpy.float),
                 ('ROBUST_RCHI2',numpy.float),
                 ('RMS',numpy.float),
                 ('ABSRESID',numpy.float,(5,)),
                 ('FRMS',numpy.float),
                 ('FABSRESID',numpy.float,(5,))
               ]


    def _run_fit_iteration(self, obj_flux, obj_ferr, obj_to_fit, weight_errors=False,
                           component_fits=False, plot=False):
        r"""
        Fit all the object spectra in obj_flux.

        Returns:
            numpy.ndarray : Array with :math:`N_{\rm spec}` instances of
            :class:`PPXFFitResult`.
        """
#        linear = fix_kinematics and mdegree < 1
        linear = False

        # Create the object to hold all the fits
        result = numpy.empty(self.nobj, dtype=object)

        # Fit each spectrum
        for i in range(self.nobj):
            print('Running pPXF fit on spectrum: {0}/{1}'.format(i+1,self.nobj), end='\r')
            # Meant to ignore this spectrum
            if not obj_to_fit[i]:
                result[i] = None
                continue

            # Get the pixels to fit for this spectrum
            gpm = numpy.where(~(obj_flux.mask[i, self.spectrum_start[i]:self.spectrum_end[i]]))[0]

            # Check if there is sufficient data for the fit
            ntpl_to_use = numpy.sum(self.tpl_to_use[i,:])
            if len(gpm) < self.dof+ntpl_to_use:
                if not self.quiet:
                    warnings.warn('Insufficient data points ({0}) to fit spectrum {1}'
                                  '(dof={2}).'.format(len(gpm), i+1, self.dof+ntpl_to_use))
                result[i] = PPXFFitResult(self.degree, self.mdegree, self.spectrum_start[i],
                                          self.spectrum_end[i], self.tpl_to_use[i,:],
                                          None, self.ntpl)
                continue

            # Run ppxf
            if plot:
                pyplot.clf()

#            print(self.tpl_comp)
#            print(self.tpl_to_use.shape[1])
#            print(numpy.sum(self.tpl_to_use[i,:]))
#            print(self.tpl_comp[self.tpl_to_use[i,:]])
#
#            print(self.comp_start_kin[i].tolist())
#            print(type(self.comp_start_kin[i].tolist()))

            result[i] = PPXFFitResult(self.degree, self.mdegree, self.spectrum_start[i],
                                      self.spectrum_end[i], self.tpl_to_use[i,:],
                            ppxf(self.tpl_flux[self.tpl_to_use[i,:],:].T,
                                 obj_flux.data[i,self.spectrum_start[i]:self.spectrum_end[i]],
                                 obj_ferr.data[i,self.spectrum_start[i]:self.spectrum_end[i]],
                                 self.velscale, self.comp_start_kin[i].tolist(), bias=self.bias, 
                                 component=self.tpl_comp[self.tpl_to_use[i,:]], degree=self.degree,
                                 goodpixels=gpm, linear=linear, mdegree=self.mdegree,
                                 moments=self.comp_moments, plot=plot, quiet=(not plot),
                                 templates_rfft=self.tpl_rfft[self.tpl_to_use[i,:],:].T,
                                 tied=self.tied, velscale_ratio=self.velscale_ratio,
                                 vsyst=-self.base_velocity[i]), self.ntpl,
                                 weight_errors=weight_errors, component_fits=component_fits)

            # TODO: check output
#            if result[i].kin[1] < 0:
#                result[i].kin[1] = numpy.absolute(result[i].kin[1]) #self.sigma_limits[0]
#                warnings.warn('pPXF gives negative dispersion! Change -{0:.4f} to {0:.4f}'.format(
#                                    result[i].kin[1]))
                
            if result[i].reached_maxiter() and not self.quiet:
                warnings.warn('pPXF optimizer reached maximum number of iterations for spectrum '
                              '{0}.'.format(i+1))
            if plot:
                pyplot.show()

        print('Running pPXF fit on spectrum: {0}/{0}'.format(self.nobj))
        return result


    def _fit_all_spectra(self, plot=False, plot_file_root=None):
        """
        Fit all spectra provided.
        """
        run_rejection = self.reject_boxcar is not None
        #---------------------------------------------------------------
        # Fit the spectra
        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO,
                       'Number of object spectra to fit: {0}/{1}'.format(
                            numpy.sum(self.obj_to_fit), len(self.obj_to_fit)))
        result = self._run_fit_iteration(self.obj_flux, self.obj_ferr, self.obj_to_fit,
                                         weight_errors=(not run_rejection),
                                         component_fits=(not run_rejection), plot=plot)
        if not run_rejection:
            # Only a single fit so return
            return result

        #---------------------------------------------------------------
        # Rejection iteration

        # Copy the input as to not overwrite the input masks
        obj_flux = self.obj_flux.copy()
        obj_ferr = self.obj_ferr.copy()
        obj_to_fit = self.obj_to_fit.copy()

        # Save which were not fit successfully
        obj_to_fit &= numpy.invert(numpy.array([ r is None or r.fit_failed() for r in result ]))
        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO,
                       'Number of object spectra to fit (excluding failed fits): {0}/{1}'.format(
                            numpy.sum(self.obj_to_fit), len(self.obj_to_fit)))

        # Reject model outliers
        obj_flux = PPXFFit.reject_model_outliers(obj_flux, result, rescale=False,
                                                 local_sigma=True, boxcar=self.reject_boxcar,
                                                 loggers=self.loggers, quiet=self.quiet)
        obj_ferr[numpy.ma.getmaskarray(obj_flux)] = numpy.ma.masked

        # Return results of refit (only ever do one rejection iteration
        return self._run_fit_iteration(obj_flux, obj_ferr, obj_to_fit, weight_errors=True,
                                       component_fits=True, plot=plot)


    def _emission_line_only_model(self, result):

        # Models originally fully masked
        model_eml_flux = numpy.ma.MaskedArray(numpy.zeros(self.obj_flux.shape, dtype=float),
                                              mask=numpy.ones(self.obj_flux.shape, dtype=bool))
        for i in range(self.nobj):
            if result[i] is None or result[i].fit_failed():
                continue
            s = result[i].start
            e = result[i].end
            model_eml_flux[i,s:e] = numpy.sum(result[i].bestfit_comp[self.gas_comp,:], axis=0)
        return model_eml_flux


    def _is_near_bounds(self, kin, kininp, vel_indx, sig_indx, lbound, ubound, tol_frac=1e-2):
        """
        Check if the fitted kinematics are near the imposed limits.
        
        The definition of "near" is that the velocity and higher moments
        cannot be closer than the provided fraction of the total width
        to the boundary.  For the velocity dispersion, the fraction is
        done in log space.
        """

        # Offset velocity: bounded by *deviations* from input value
        _lbound = lbound
        _lbound[vel_indx] += kininp[vel_indx]
        _ubound = ubound
        _ubound[vel_indx] += kininp[vel_indx]

        # Set the tolerance
        Db = ubound-lbound
        Db[sig_indx] = numpy.log10(ubound[sig_indx])-numpy.log10(lbound[sig_indx])
        tol = Db*tol_frac

        # Determine if the parameter is near the lower boundary (only
        # relevant to the sigma) ... 
        near_lower_bound = kin - _lbound < tol
        # and whether it's close to either
        near_bound = near_lower_bound | (_ubound - kin < tol)

        # Return the two boundary flags
        return near_bound, near_lower_bound


    def _validate_dispersions(self, model_eml_par, rng=[0,400]):
        """
        Check that the corrected velocity dispersion are in the provided range.
        """
        _rng = numpy.square(rng)
        _fit_eml = numpy.ones(model_eml_par['SIGMACORR'].shape, dtype=numpy.bool)
        _fit_eml[:,numpy.invert(self.fit_eml)] = False
        sigcor = numpy.square(model_eml_par['KIN'][:,:,1]) \
                        - numpy.square(model_eml_par['SIGMACORR'][:,:])
        indx = ((sigcor < _rng[0]) | (sigcor > _rng[1])) & _fit_eml
        if numpy.sum(indx) == 0:
            return
        model_eml_par['MASK'][indx] = self.bitmask.turn_on(model_eml_par['MASK'][indx], 'BAD_SIGMA')

        return model_eml_par


    def _save_results(self, etpl, result, model_mask, model_fit_par, model_eml_par):
        """
        Save the results of the ppxf fit for each spectrum to the model
        spectra and model emission line paramters.  Also modify the
        mask.

        Much of this is the same as
        :class:`mangadap.proc.ppxffit.PPXFFit._save_results`.
        """
        #---------------------------------------------------------------
        # Get the model spectra
        model_flux = PPXFFit.compile_model_flux(self.obj_flux, result)
        model_eml_flux = self._emission_line_only_model(result)

        # Save the pixel statistics
        model_fit_par['BEGPIX'] = self.spectrum_start
        model_fit_par['ENDPIX'] = self.spectrum_end
        model_fit_par['NPIXTOT'] = self.spectrum_end - self.spectrum_start

        # Calculate the model residuals, which are masked where the data
        # were not fit
        residual = self.obj_flux - model_flux
        fractional_residual = numpy.ma.divide(self.obj_flux - model_flux, model_flux)
        # Get the chi-square for each spectrum
        model_fit_par['CHI2'] = numpy.sum(numpy.square(residual/self.obj_ferr), axis=1)
        # Get the (fractional) residual RMS for each spectrum
        model_fit_par['RMS'] = numpy.sqrt(numpy.ma.mean(numpy.square(residual), axis=1))
        model_fit_par['FRMS'] = numpy.sqrt(numpy.ma.mean(numpy.square(fractional_residual), axis=1))

        # Flag the pixels that were not used
        model_mask[numpy.ma.getmaskarray(self.obj_flux)] \
                        = self.bitmask.turn_on(model_mask[numpy.ma.getmaskarray(self.obj_flux)],
                                               flag='DIDNOTUSE')

        # Mask any lines that were not fit
        model_eml_par['MASK'][:,numpy.invert(self.fit_eml)] \
                    = self.bitmask.turn_on(model_eml_par['MASK'][:,numpy.invert(self.fit_eml)],
                                           flag='NO_FIT')

        # Generate some convenience data:
        #  - Get the list of indices in the flatted kinematics vectors
        #    with the *unfixed*, *defining* parameters used for each
        #    kinematic measurement.  These are used to set the
        #    kinematics and errors for each emission line.
        #  - Generate vectors with the lower and upper bounds for the
        #    kinematic parameters
        #  - Flag parameters that are velocity and sigma components
        lboundi = [ self.velocity_limits[0], self.sigma_limits[0], self.gh_limits[0],
                    self.gh_limits[0], self.gh_limits[0], self.gh_limits[0] ]
        uboundi = [ self.velocity_limits[1], self.sigma_limits[1], self.gh_limits[1],
                    self.gh_limits[1], self.gh_limits[1], self.gh_limits[1] ]
        lbound = []
        ubound = []
        par_indx = []
        vel_indx = numpy.zeros(self.npar_kin, dtype=bool)
        sig_indx = numpy.zeros(self.npar_kin, dtype=bool)
        for j in range(self.ncomp):
            start = numpy.sum(numpy.absolute(self.comp_moments[:j]))
            nmom = numpy.absolute(self.comp_moments[j])
            par_indx += [ [0]*nmom ]
            for k in range(nmom):
                par_indx[j][k] = start+k if len(self.tied[j][k]) == 0 \
                                        else int(self.tied[j][k].split('[')[1].split(']')[0])
            vel_indx[par_indx[j][0]] = True
            sig_indx[par_indx[j][1]] = True
            lbound += [ lboundi[:nmom] ]
            ubound += [ uboundi[:nmom] ]
        lbound = numpy.concatenate(tuple(lbound))
        ubound = numpy.concatenate(tuple(ubound))

        # The set of gas templates, and the kinematic component,
        # velocity group, and sigma group associated with each
        # template are the same for all fits
        model_fit_par['KINCMP'] = numpy.array([self.tpl_comp]*self.nobj)
        model_fit_par['VELCMP'] = numpy.array([self.tpl_vgrp]*self.nobj)
        model_fit_par['SIGCMP'] = numpy.array([self.tpl_sgrp]*self.nobj)
        model_fit_par['TIEDKIN'] = numpy.array([numpy.concatenate(tuple(par_indx))]*self.nobj)

        #---------------------------------------------------------------
        # Need to iterate over each spectrum
        for i in range(self.nobj):

            #-----------------------------------------------------------
            # Set output flags
            # - No fit was performed
            if result[i] is None:
                model_mask[i,:] = self.bitmask.turn_on(model_mask[i,:], 'NO_FIT')
                continue

            # - No fit attempted because of insufficient data
            if result[i].empty_fit():
                model_mask[i,:] = self.bitmask.turn_on(model_mask[i,:], 'NO_FIT')
                model_fit_par['MASK'][i] = self.bitmask.turn_on(model_fit_par['MASK'][i],
                                                                'INSUFFICIENT_DATA')
                model_eml_par['MASK'][i] = self.bitmask.turn_on(model_eml_par['MASK'][i],
                                                                'INSUFFICIENT_DATA')
                continue

            # - Fit attempted but failed
            if result[i].fit_failed():
                model_mask[i,:] = self.bitmask.turn_on(model_mask[i,:], 'FIT_FAILED')
                model_fit_par['MASK'][i] = self.bitmask.turn_on(model_fit_par['MASK'][i],
                                                                'FIT_FAILED')
                model_eml_par['MASK'][i] = self.bitmask.turn_on(model_eml_par['MASK'][i],
                                                                'FIT_FAILED')

            # - Fit successful but hit maximum iterations.
            if result[i].reached_maxiter():
                model_fit_par['MASK'][i] = self.bitmask.turn_on(model_fit_par['MASK'][i], 'MAXITER')
                model_eml_par['MASK'][i] = self.bitmask.turn_on(model_eml_par['MASK'][i], 'MAXITER')

            # - Mask rejected pixels
            original_gpm = numpy.where(numpy.invert(
                               numpy.ma.getmaskarray(self.obj_flux)[i,self.spectrum_start[i]
                                                                      :self.spectrum_end[i]]))[0]
            rejected_pixels = list(set(original_gpm) - set(result[i].gpm))
            if len(rejected_pixels) > 0:
                model_mask[i,self.spectrum_start[i]:self.spectrum_end[i]][rejected_pixels] \
                        = self.bitmask.turn_on(model_mask[i,self.spectrum_start[i]:
                                                            self.spectrum_end[i]][rejected_pixels],
                                               flag=PPXFFit.rej_flag)

            #-----------------------------------------------------------
            # Save the model parameters and figures of merit
            # - Number of fitted pixels
            model_fit_par['NPIXFIT'][i] = len(result[i].gpm)
            # - Templates used
            model_fit_par['USETPL'][i] = result[i].tpl_to_use
            # - Template weights
            model_fit_par['TPLWGT'][i][result[i].tpl_to_use] = result[i].tplwgt
            # Additive polynomial coefficients
            if self.degree >= 0 and result[i].addcoef is not None:
                model_fit_par['ADDCOEF'][i] = result[i].addcoef
            if self.mdegree > 0 and result[i].multcoef is not None:
                model_fit_par['MULTCOEF'][i] = result[i].multcoef
            # Flattened input kinematics vector
            model_fit_par['KININP'][i] = numpy.concatenate(tuple(self.comp_start_kin[i]))
            # Flattened best-fit kinematics vector
            model_fit_par['KIN'][i] = numpy.concatenate(tuple(result[i].kin))
            # Flattened kinematic error vector
            model_fit_par['KINERR'][i] = numpy.concatenate(tuple(result[i].kinerr))
            # Chi-square
            model_fit_par['RCHI2'][i] = model_fit_par['CHI2'][i] \
                                        / (model_fit_par['NPIXFIT'][i] 
                                            - self.dof - numpy.sum(model_fit_par['TPLWGT'][i] > 0))
            model_fit_par['ROBUST_RCHI2'][i] = result[i].robust_rchi2

            # Get growth statistics for the residuals
            model_fit_par['ABSRESID'][i] = residual_growth((residual[i,:]).compressed(),
                                                       [0.68, 0.95, 0.99])
            model_fit_par['FABSRESID'][i] = residual_growth(fractional_residual[i,:].compressed(),
                                                        [0.68, 0.95, 0.99])

            #-----------------------------------------------------------
            # Test if the kinematics are near the imposed boundaries.
            near_bound, near_lower_bound = self._is_near_bounds(model_fit_par['KIN'][i],
                                                                model_fit_par['KININP'][i],
                                                                vel_indx, sig_indx, lbound, ubound)

            # Add the *global* flag for the fit.
            # TODO: These are probably too general.
            # - If the velocity dispersion has hit the lower limit, ONLY
            #   flag the value as having a MIN_SIGMA.
            if numpy.any(near_lower_bound & sig_indx):
                model_fit_par['MASK'][i] = self.bitmask.turn_on(model_fit_par['MASK'][i],
                                                                'MIN_SIGMA')
            # - Otherwise, flag the full fit as NEAR_BOUND, both the
            #   parameters and the model
            if numpy.any((near_lower_bound & numpy.invert(sig_indx)) 
                                | (near_bound & numpy.invert(near_lower_bound))):
                model_fit_par['MASK'][i] = self.bitmask.turn_on(model_fit_par['MASK'][i],
                                                                'NEAR_BOUND')
                model_mask[i,:] = self.bitmask.turn_on(model_mask[i,:], 'NEAR_BOUND')

            # Convert the velocities from pixel units to cz
            model_fit_par['KININP'][i,vel_indx], _ \
                        = PPXFFit.convert_velocity(model_fit_par['KININP'][i,vel_indx],
                                                   numpy.zeros(numpy.sum(vel_indx)))
            model_fit_par['KIN'][i,vel_indx], model_fit_par['KINERR'][i,vel_indx] \
                        = PPXFFit.convert_velocity(model_fit_par['KIN'][i,vel_indx],
                                                   model_fit_par['KINERR'][i,vel_indx])

            # Divvy up the fitted parameters into the result for each
            # emission line
            for j in range(self.neml):
                if not self.fit_eml[j]:
                    continue

                # The "fit index" is the component of the line
                model_eml_par['FIT_INDEX'][i,j] = self.eml_compi[j]

                # EmissionLineTemplates constructs each line to have the
                # flux provided by the emission-line database
                model_eml_par['FLUX'][i,j] = result[i].tplwgt[self.eml_compi[j]] \
                                                * self.emldb['flux'][j]
                model_eml_par['FLUXERR'][i,j] = result[i].tplwgterr[self.eml_compi[j]] \
                                                * self.emldb['flux'][j]

                # Use the flattened vectors to set the kinematics
                indx = par_indx[self.eml_compi[j]]
                model_eml_par['KIN'][i,j,:] = model_fit_par['KIN'][i,indx]
                model_eml_par['KINERR'][i,j,:] = model_fit_par['KINERR'][i,indx]

                # Get the bound masks specific to this emission-line (set)
                if numpy.any(near_lower_bound[indx] & sig_indx[indx]):
                    model_eml_par['MASK'][i,j] = self.bitmask.turn_on(model_eml_par['MASK'][i,j],
                                                                      'MIN_SIGMA')
                if numpy.any((near_lower_bound[indx] & numpy.invert(sig_indx[indx])) 
                                | (near_bound[indx] & numpy.invert(near_lower_bound[indx]))):
                    model_eml_par['MASK'][i,j] = self.bitmask.turn_on(model_eml_par['MASK'][i,j],
                                                                      'NEAR_BOUND')

            # Get the instrumental dispersion in the galaxy data at the
            # location of the fitted lines
            sigma_inst = EmissionLineFit.instrumental_dispersion(self.obj_wave, self.obj_sres[i,:],
                                                        self.emldb['restwave'][self.fit_eml],
                                                        model_eml_par['KIN'][i,self.fit_eml,0])
#            pyplot.scatter(self.emldb['restwave'][self.fit_eml], sigma_inst, marker='.', s=50)
#            pyplot.scatter(self.emldb['restwave'][self.fit_eml], etpl.eml_sigma_inst[self.fit_eml], marker='.', s=50)
#            pyplot.show()

            # The dispersion correction is the quadrature difference
            # between the instrumental dispersion in the galaxy data to
            # the dispersion used when constructing the emission-line
            # templates
            sigma2corr = numpy.square(sigma_inst) - numpy.square(etpl.eml_sigma_inst[self.fit_eml])
            if numpy.any(sigma2corr < 0):
                print(sigma2corr)
                warnings.warn('Encountered negative sigma corrections!')
            model_eml_par['SIGMACORR'][i,self.fit_eml] = numpy.ma.sqrt(numpy.square(sigma_inst)
                                    - numpy.square(etpl.eml_sigma_inst[self.fit_eml])).filled(0.0)
#            print(model_eml_par['SIGMACORR'][i])

        #---------------------------------------------------------------
        # Test if kinematics are reliable
        model_eml_par = self._validate_dispersions(model_eml_par)

        #---------------------------------------------------------------
        # Return the fitting results
        # - model_flux: full model fit to the spectra
        # - model_eml_flux: emission-line only model
        # - model_mask: Bitmask spectra for the fit
        # - model_fit_par: The saved results from the ppxf fit
        # - model_eml_par: The fit results parsed into data for each
        #   emission line
        return model_flux, model_eml_flux, model_mask, model_fit_par, model_eml_par



    def fit_SpatiallyBinnedSpectra(self, binned_spectra, par=None, loggers=None, quiet=False):
        """
        This is a basic interface that is geared for the DAP that
        interacts with the rest of the, more general, parts of the
        class.

        This should not declare anything to self!

        .. todo::
            Use waverange in pixel mask to restrict wavelength range.
            Add to SasukePar.

        Args:
            binned_spectra
                (:class:`mangadap.proc.spatiallybinnedspectra.SpatiallyBinnedSpectra`):
                Spectra to fit.
            par (SasukePar): Parameters provided from the DAP to the
                general Sasuke fitting algorithm (:func:`fit`).
            loggers (list): (**Optional**) List of `logging.Logger`_ objects
                to log progress; ignored if quiet=True.  Logging is done
                using :func:`mangadap.util.log.log_output`.  Default is
                no logging.
            quiet (bool): (**Optional**) Suppress all terminal and
                logging output.  Default is False.


        Returns:
            numpy.ma.MaskedArray: model_wave,
                                  model_flux,
                                  model_base,
                                  model_mask,
                                  model_fit_par,
                                  model_eml_par
        """
        # Assign the parameters if provided
        if par is None:
            raise ValueError('Must provide parameters!')
        if not isinstance(par, SasukePar):
            raise TypeError('Input parameters must be an instance of SasukePar.')
        # SasukePar checks the types of the stellar continuum,
        # emission-line database, and pixel mask

        # SpatiallyBinnedSpectra object always needed
        if binned_spectra is None:
            raise ValueError('Must provide spectra object for fitting.')
        if not isinstance(binned_spectra, SpatiallyBinnedSpectra):
            raise TypeError('Must provide a valid SpatiallyBinnedSpectra object!')
        if binned_spectra.hdu is None:
            raise ValueError('Provided SpatiallyBinnedSpectra object is undefined!')

        # Get the data arrays to fit
        # TODO: This could be where we pull out the individual spaxels
        # and renormalize the continuum fit from the binned data.  For
        # now this just pulls out the binned spectra
        flux, ferr = EmissionLineFit.get_spectra_to_fit(binned_spectra, pixelmask=par['pixelmask'],
                                                        error=True)
        # TODO: Also may want to include pixels rejected during stellar
        # kinematics fit
        nobj = flux.shape[0]
#        print(nobj)

        # Get the stellar templates
        # TODO: This could be where we instead construct the
        # optimal template used for each spectrum.
        stellar_templates = None if par['stellar_continuum'] is None else \
                                par['stellar_continuum'].method['fitpar']['template_library']
        stpl_wave = None if par['stellar_continuum'] is None else stellar_templates['WAVE'].data
        stpl_flux = None if par['stellar_continuum'] is None else stellar_templates['FLUX'].data
        if not quiet:
            warnings.warn('Adopting mean spectral resolution of all templates!')
        stpl_sres = None if par['stellar_continuum'] is None \
                        else numpy.mean(stellar_templates['SPECRES'].data, axis=0).ravel()
        velscale_ratio = 1 if par['stellar_continuum'] is None \
                                else par['stellar_continuum'].method['fitpar']['velscale_ratio']
        matched_resolution = False if par['stellar_continuum'] is None \
                                else par['stellar_continuum'].method['fitpar']['match_resolution']

        # Get the stellar kinematics
        # TODO: This could be where we pull out the valid fits and then
        # interpolate (or some other approach) to spaxels without direct
        # stellar kinematics measurements.  For now this just pulls out
        # the data for the binned spectra and replaces those results for
        # spectra with insufficient S/N or where pPXF failed to the
        # median redshift/dispersion of the fitted spectra.  We'll need
        # to revisit this
        stellar_velocity, stellar_dispersion = (None, None) if par['stellar_continuum'] is None \
                        else par['stellar_continuum'].matched_guess_kinematics(binned_spectra,
                                                                               cz=True)
        stellar_kinematics = None if stellar_velocity is None or stellar_dispersion is None \
                                else numpy.array([ stellar_velocity, stellar_dispersion ]).T
#        print(stellar_redshift)
#        print(stellar_dispersion)
#        print(stellar_redshift.shape)

        # Set which stellar templates to use for each spectrum
        # TODO: Here I'm flagging to only use the stellar templates used
        # in the stellar kinematics fit.  You could select to use all
        # templates by setting stpl_to_use=None below, or we could construct
        # the optimal template for each spectrum and just have a single
        # stellar template used in the emission-line fits.
        stpl_to_use = None if par['stellar_continuum'] is None \
                        else par['stellar_continuum'].matched_template_flags(binned_spectra)
#        stpl_to_use = None

        # Get the spectra that meet the S/N criterion
        # TODO: This could be based on the moment assessment of the
        # emission-line S/N instead; for now just based on continuum
        # S/N.
        good_snr = binned_spectra.above_snr_limit(par['minimum_snr'])

        # Determine which spectra have a valid stellar continuum fit
        good_stellar_continuum_fit = numpy.invert(
                    par['stellar_continuum'].bitmask.flagged(
                                            par['stellar_continuum']['PAR'].data['MASK'],
                                            flag=[ 'NO_FIT', 'INSUFFICIENT_DATA', 'FIT_FAILED']))

        # TODO: At the moment, spectra to fit must have both good S/N
        # and a stellar continuum fit
        spec_to_fit = good_snr & good_stellar_continuum_fit

        # TODO: For now can only fit two moments
        if par['moments'] != 2:
            print(par['moments'])
            raise NotImplementedError('Number of gas moments can only be two.')

        # Return the fitted data
        model_wave, model_flux, model_eml_flux, model_mask, model_fit_par, model_eml_par \
                = self.fit(par['emission_lines'], binned_spectra['WAVE'].data, flux[spec_to_fit,:],
                           obj_ferr=ferr[spec_to_fit,:],
                           obj_sres=binned_spectra['SPECRES'].data.copy()[spec_to_fit,:],
                           guess_redshift=par['guess_redshift'][spec_to_fit],
                           guess_dispersion=par['guess_dispersion'][spec_to_fit],
                           reject_boxcar=par['reject_boxcar'],
                           stpl_wave=stpl_wave, stpl_flux=stpl_flux, stpl_sres=stpl_sres,
                           stpl_to_use=None if stpl_to_use is None else stpl_to_use[spec_to_fit,:],
                           stellar_kinematics=None if stellar_kinematics is None else
                                        stellar_kinematics[spec_to_fit,:],
                           velscale_ratio=velscale_ratio, matched_resolution=matched_resolution,
                           bias=par['bias'], degree=par['degree'], mdegree=par['mdegree'],
                           #moments=par['moments'],
                           loggers=loggers, quiet=quiet)
        # Save the the bin ID numbers indices based on the spectra
        # selected to be fit
        model_fit_par['BINID'] = binned_spectra['BINS'].data['BINID'][spec_to_fit]
        model_fit_par['BINID_INDEX'] = numpy.arange(binned_spectra.nbins)[spec_to_fit]

        model_eml_par['BINID'] = binned_spectra['BINS'].data['BINID'][spec_to_fit]
        model_eml_par['BINID_INDEX'] = numpy.arange(binned_spectra.nbins)[spec_to_fit]

        # Add the equivalent width data
        EmissionLineFit.measure_equivalent_width(binned_spectra['WAVE'].data, flux[spec_to_fit,:],
                                                 par['emission_lines'], model_eml_par,
                                                 redshift=par['guess_redshift'][spec_to_fit],
                                                 bitmask=self.bitmask, checkdb=False)
        eml_continuum = model_flux-model_eml_flux

        # Use the previous fit to the stellar continuum to set the
        # emission-line model "baseline"

        # TODO: This is done as a place holder.  We need a better way of
        # propagating the difference between the stellar-kinematics fit
        # and the combined stellar-continuum + emission-line model to
        # the output datamodel.
        stellar_continuum = numpy.zeros(flux.shape, dtype=float) \
                            if par['stellar_continuum'] is None \
                            else par['stellar_continuum'].fill_to_match(binned_spectra).filled(0.0)

        model_eml_base = model_flux-model_eml_flux-stellar_continuum[spec_to_fit,:]
        model_eml_flux += model_eml_base

#        pyplot.plot(model_wave, flux[spec_to_fit,:][0,:], color='k', lw=1, zorder=1)
#        pyplot.plot(model_wave, stellar_continuum[spec_to_fit,:][0,:], color='C0', lw=1.0,
#                    zorder=2)
#        pyplot.plot(model_wave, model_eml_flux[0,:], color='C1', lw=1.0, zorder=3)
#        pyplot.plot(model_wave, model_eml_base[0,:], color='C2', lw=1.0, zorder=4)
#        pyplot.plot(model_wave, eml_continuum[0,:], color='C3', lw=1.0, zorder=4)
#        pyplot.show()
        
        # Only return model and model parameters for the *fitted*
        # spectra
        return model_wave, model_eml_flux, model_eml_base, model_mask, model_fit_par, model_eml_par


    def fit(self, emission_lines, obj_wave, obj_flux, obj_ferr=None, mask=None, obj_sres=None,
            guess_redshift=None, guess_dispersion=None, reject_boxcar=None, stpl_wave=None,
            stpl_flux=None, stpl_sres=None, stpl_to_use=None, stellar_kinematics=None,
            velscale_ratio=None, matched_resolution=True, waverange=None, bias=None, degree=4,
            mdegree=0, max_velocity_range=400., alias_window=None, dvtol=1e-10, loggers=None,
            quiet=False, plot=False):
            #moments=2,
        """
        Fit a set of emission lines using pPXF.
        
        The flux array is expected to have size Nspec x Nwave.

        .. todo::
        
            - Allow for moments != 2.
            - Allow for fixed components to be set from emission-line
              database
            - Allow for bounds to be set from emission-line database

        Raises:
            ValueError: Raised if the length of the spectra, errors, or
                mask does not match the length of the wavelength array;
                raised if the wavelength, redshift, or dispersion arrays
                are not 1D vectors; and raised if the number of
                redshifts or dispersions is not a single value or the
                same as the number of input spectra.
        """
        #---------------------------------------------------------------
        # Initialize the reporting
        if loggers is not None:
            self.loggers = loggers
        self.quiet = quiet

        #---------------------------------------------------------------
        # Check the input data
        self.obj_wave, self.obj_flux, self.obj_ferr, self.obj_sres \
                = PPXFFit.check_objects(obj_wave, obj_flux, obj_ferr=obj_ferr, obj_sres=obj_sres)
        self.nobj, self.npix_obj = self.obj_flux.shape
        self.waverange = PPXFFit.set_wavelength_range(self.nobj, self.obj_wave, waverange)
        self.input_obj_mask = numpy.ma.getmaskarray(self.obj_flux).copy()
        self.obj_to_fit = numpy.any(numpy.invert(self.input_obj_mask), axis=1)
        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO,
                       'Number of object spectra to fit: {0}/{1}'.format(
                            numpy.sum(self.obj_to_fit), len(self.obj_to_fit)))
        self.input_cz, guess_kin = PPXFFit.check_input_kinematics(self.nobj, guess_redshift,
                                                                  guess_dispersion)

        #---------------------------------------------------------------
        # Compare pixel scales and set template wavelength vector
        if stpl_wave is not None:
            self.velscale, self.velscale_ratio \
                    = PPXFFit.check_pixel_scale(stpl_wave, self.obj_wave,
                                                velscale_ratio=velscale_ratio, dvtol=dvtol)
            self.tpl_wave = stpl_wave
        else:
            self.velscale = spectrum_velocity_scale(self.obj_wave)
            self.velscale_ratio = 1
            self.tpl_wave = self.obj_wave

        #---------------------------------------------------------------
        # Check any input stellar template spectra
        # self.tpl_sres has type
        # mangadap.util.instrument.SpectralResolution!
        if stpl_flux is not None:
            if stpl_wave is None:
                raise ValueError('Must provide wavelengths if providing stellar template fluxes.')
            self.tpl_wave, self.tpl_flux, self.tpl_sres \
                    = PPXFFit.check_templates(stpl_wave, stpl_flux, tpl_sres=stpl_sres,
                                              velscale_ratio=self.velscale_ratio)
            self.nstpl = self.tpl_flux.shape[0]
            # Check or instantiate the fit flags
            self.tpl_to_use = PPXFFit.check_template_usage_flags(self.nobj, self.nstpl, stpl_to_use)
        else:
            self.tpl_flux = None
            self.tpl_sres = None
            self.nstpl = 0
            self.tpl_to_use = None

        #---------------------------------------------------------------
        # Set the template spectral resolution.  This is needed to
        # construct the emission-line templates:
        # - Set to the stellar template resolution of that is provided
        #   (done above)
        # - If the object resolution is not available, set such that the
        #   template lines will be initialized with sigma = 1 pixel.
        # - If the object resolution is available, set to the minimum
        #   object resolution at each wavelength channel.
        convertR = astropy.constants.c.to('km/s').value / DAPConstants.sig2fwhm
        self.matched_resolution = matched_resolution
        if self.tpl_sres is None:
            self.matched_resolution = False
            self.tpl_sres = numpy.full(self.npix_obj, convertR/self.velscale, dtype=float) \
                                if self.obj_sres is None else numpy.amin(self.obj_sres, axis=1)
            self.tpl_sres = SpectralResolution(self.tpl_wave, self.tpl_sres, log10=True)

        #---------------------------------------------------------------
        # If provided, check the shapes of the stellar kinematics
        if stellar_kinematics is not None and stellar_kinematics.shape[0] != self.nobj:
            raise ValueError('Provided kinematics do not match the number of input object spectra.')
        stellar_moments = None if stellar_kinematics is None else stellar_kinematics.shape[1]
        if self.nstpl > 0 and stellar_kinematics is None:
            raise ValueError('Must provide stellar kinematics if refiting stellar templates.')
        # Convert velocities from cz to pPXF pixelized velocities
        if self.nstpl > 0:
            _stellar_kinematics = stellar_kinematics.copy()
            _stellar_kinematics[:,0], _ = PPXFFit.revert_velocity(stellar_kinematics[:,0],
                                                                  numpy.zeros(self.nobj))

        #---------------------------------------------------------------
        # Build the emission-line templates; the EmissionLineTemplates
        # object will check the database
        self.emldb = emission_lines
        self.neml = self.emldb.neml
        etpl = EmissionLineTemplates(self.tpl_wave, convertR/self.tpl_sres.sres(), emldb=self.emldb,
                                     loggers=self.loggers, quiet=self.quiet)
        # Set the component associated with each emission line emission line
        self.fit_eml = self.emldb['action'] == 'f'
        self.eml_tpli = etpl.tpli.copy()
        self.eml_compi = numpy.full(self.neml, -1, dtype=int)
        self.eml_compi[self.fit_eml] = numpy.array([ etpl.comp[i] for i in etpl.tpli[self.fit_eml]])

        #---------------------------------------------------------------
        # Save the basic pPXF parameters
        # TODO: Use the emission-line database to set the number of
        # moments to fit to each emission-line component.  For now it is
        # always moments=2!
        self.velocity_limits, self.sigma_limits, self.gh_limits \
                    = PPXFFit.losvd_limits(self.velscale)
        self.bias = bias
        self.degree = degree
        self.mdegree = mdegree
        moments = 2                #numpy.absolute(moments)
        self.reject_boxcar = reject_boxcar
#        self.fix_kinematics = False     #moments < 0

        #---------------------------------------------------------------
        # Compile the template fluxes, components, and velocity and
        # sigma groups
        if self.nstpl == 0:
            self.tpl_flux = etpl.flux
            self.tpl_to_use = numpy.ones((self.nobj,etpl.ntpl), dtype=numpy.bool)
            self.tpl_comp = etpl.comp
            self.tpl_vgrp = etpl.vgrp
            self.tpl_sgrp = etpl.sgrp
            self.ncomp = numpy.amax(self.tpl_comp)+1
            self.comp_moments = numpy.array([moments]*self.ncomp)
            self.comp_start_kin = numpy.array([[gk.tolist()]*self.ncomp for gk in guess_kin ])
        else:
            self.tpl_flux = numpy.append(self.tpl_flux, etpl.flux, axis=0)
            self.tpl_to_use = numpy.append(self.tpl_to_use,
                                           numpy.ones((self.nobj,etpl.ntpl), dtype=numpy.bool),
                                           axis=1)
            self.tpl_comp = numpy.append(numpy.zeros(self.nstpl, dtype=int), etpl.comp+1)
            self.tpl_vgrp = numpy.append(numpy.zeros(self.nstpl, dtype=int), etpl.vgrp+1)
            self.tpl_sgrp = numpy.append(numpy.zeros(self.nstpl, dtype=int), etpl.sgrp+1)
            self.eml_tpli[self.fit_eml] += 1
            self.eml_compi[self.fit_eml] += 1
            self.ncomp = numpy.amax(self.tpl_comp)+1
            self.comp_moments = numpy.array([-stellar_moments] + [moments]*(self.ncomp-1))
            self.comp_start_kin = numpy.array([ [sk.tolist()] + [gk.tolist()]*(self.ncomp-1) 
                                            for sk,gk in zip(_stellar_kinematics, guess_kin) ])
        self.ntpl, self.npix_tpl = self.tpl_flux.shape
        self.tpl_npad = fftpack.next_fast_len(self.npix_tpl)
        self.tpl_rfft = numpy.fft.rfft(self.tpl_flux, self.tpl_npad, axis=1)

        # Set which components are gas components
        self.gas_comp = numpy.ones(self.ncomp, dtype=bool)
        if self.nstpl > 0:
            self.gas_comp[0] = False

        #---------------------------------------------------------------
        # Parse the velocity and sigma groups into tied parameters
        self.npar_kin = numpy.sum(numpy.absolute(self.comp_moments))
        self.tied = numpy.empty(self.npar_kin, dtype=object)
        tpl_index = numpy.arange(self.ntpl)
        for i in range(self.ncomp):
            # Do not allow tying to fixed components?
            if self.comp_moments[i] < 0:
                continue
            # Velocity group of this component
            indx = self.tpl_comp[tpl_index[self.tpl_vgrp == i]]
            if len(indx) > 1:
                parn = [ 0 + numpy.sum(numpy.absolute(self.comp_moments[:i])) for i in indx ]
                self.tied[parn[1:]] = 'p[{0}]'.format(parn[0])
            
            # Sigma group of this component
            indx = self.tpl_comp[tpl_index[self.tpl_sgrp == i]]
            if len(indx) > 1:
                parn = [ 1 + numpy.sum(numpy.absolute(self.comp_moments[:i])) for i in indx ]
                self.tied[parn[1:]] = 'p[{0}]'.format(parn[0])

        self.tied[[t is None for t in self.tied ]] = ''
        self.nfree_kin = numpy.sum([len(t) == 0 for t in self.tied])

        # Get the degrees of freedom (accounting for fixed stellar
        # component)
        self.dof = self.nfree_kin + numpy.sum(self.comp_moments[self.comp_moments < 0]) \
                        + max(self.mdegree, 0)
        if self.degree >= 0:
            self.dof += self.degree+1

        # Check if tying parameters is needed
        if self.nfree_kin == self.npar_kin:
            self.tied = None
        else:
            start = [numpy.sum(numpy.absolute(self.comp_moments[:i])) for i in range(self.ncomp) ]
            self.tied = [ self.tied[start[i] : 
                          start[i]+numpy.absolute(self.comp_moments[i])].tolist()
                            for i in range(self.ncomp) ]

        #---------------------------------------------------------------
        # Report the input checks/results
        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO, 'Pixel scale: {0} km/s'.format(self.velscale))
            log_output(self.loggers, 1, logging.INFO, 'Pixel scale ratio: {0}'.format(
                                                                            self.velscale_ratio))
            log_output(self.loggers, 1, logging.INFO, 'Dispersion limits: {0} - {1}'.format(
                                                                            *self.sigma_limits))
            log_output(self.loggers, 1, logging.INFO, 'Model degrees of freedom: {0}'.format(
                                                                            self.dof+self.ntpl))
            log_output(self.loggers, 1, logging.INFO, 'Number of tied parameters: {0}'.format(
                            0 if self.tied is None else
                            self.nfree_kin + numpy.sum(self.comp_moments[self.comp_moments < 0])))

        #---------------------------------------------------------------
        # Initialize the output arrays.  This is done here for many of
        # these objects only in case PPXFFit.initialize_pixels_to_fit()
        # fails and the code returns without running the pPXF fitting.
        #  - Model flux
        model_flux = numpy.zeros(self.obj_flux.shape, dtype=numpy.float)
        model_eml_flux = numpy.zeros(self.obj_flux.shape, dtype=numpy.float)
        #  - Model mask:
        model_mask = numpy.zeros(self.obj_flux.shape, dtype=self.bitmask.minimum_dtype())
        indx = numpy.ma.getmaskarray(self.obj_flux)
        model_mask[indx] = self.bitmask.turn_on(model_mask[indx], 'DIDNOTUSE')
        #  - Model parameters and fit quality
        model_fit_par = init_record_array(self.nobj,
                                          self._per_fit_dtype(self.ntpl, self.degree+1,
                                          self.mdegree, self.npar_kin,
                                          self.bitmask.minimum_dtype()))
        model_fit_par['BINID'] = numpy.arange(self.nobj)
        model_fit_par['BINID_INDEX'] = numpy.arange(self.nobj)
        #  - Model emission-line parameters
        model_eml_par = init_record_array(self.nobj,
                                          self._per_emission_line_dtype(self.neml, 2,
                                                                self.bitmask.minimum_dtype()))
        model_eml_par['BINID'] = numpy.arange(self.nobj)
        model_eml_par['BINID_INDEX'] = numpy.arange(self.nobj)

        #---------------------------------------------------------------
        # Initialize the mask and the spectral range to fit
        model_mask, err, self.spectrum_start, self.spectrum_end \
                = PPXFFit.initialize_pixels_to_fit(self.tpl_wave, self.obj_wave, self.obj_flux,
                                                   self.obj_ferr, self.velscale,
                                                   velscale_ratio=self.velscale_ratio,
                                                   waverange=waverange, mask=mask,
                                                   bitmask=self.bitmask,
                                                   velocity_offset=self.input_cz,
                                                   max_velocity_range=max_velocity_range,
                                                   alias_window=alias_window, ensemble=False,
                                                   loggers=self.loggers, quiet=self.quiet)
        ended_in_error = numpy.array([e is not None for e in err])
        if numpy.any(ended_in_error):
            if not self.quiet:
                warnings.warn('Masking failures in some/all spectra.  Errors are: {0}'.format(
                                numpy.array([(i,e) for i,e in enumerate(err)])[ended_in_error]))
            model_fit_par['MASK'][ended_in_error] \
                    = self.bitmask.turn_on(model_fit_par['MASK'][ended_in_error], 'NO_FIT')
            model_eml_par['MASK'][ended_in_error] \
                    = self.bitmask.turn_on(model_eml_par['MASK'][ended_in_error], 'NO_FIT')
        if numpy.all(ended_in_error):
            return self.obj_wave, model_flux, model_eml_flux, model_mask, model_eml_par

        #---------------------------------------------------------------
        # Get the input pixel shift between the object and template
        # wavelength vectors; interpretted by pPXF as a base velocity
        # shift between the two
        self.base_velocity = numpy.array([PPXFFit.ppxf_tpl_obj_voff(self.tpl_wave,
                                                            self.obj_wave[s:e], self.velscale,
                                                            velscale_ratio=self.velscale_ratio)
                                                for s,e in zip(self.spectrum_start,
                                                               self.spectrum_end)])

        #---------------------------------------------------------------
        # Fit all spectra
        t = time.clock()
#        warnings.warn('debugging!')
#        self.obj_to_fit[ numpy.arange(self.nobj)[self.obj_to_fit][2:] ] = False
        result = self._fit_all_spectra(plot=plot)#, plot_file_root=plot_file_root)
        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO, 'Fits completed in {0:.4e} min.'.format(
                       (time.clock() - t)/60))

        #---------------------------------------------------------------
        # Save the results
        model_flux, model_eml_flux, model_mask, model_fit_par, model_eml_par \
                = self._save_results(etpl, result, model_mask, model_fit_par, model_eml_par)

        if not self.quiet:
            log_output(self.loggers, 1, logging.INFO, 'Sasuke finished')

        return self.obj_wave, model_flux, model_eml_flux, model_mask, model_fit_par, model_eml_par



