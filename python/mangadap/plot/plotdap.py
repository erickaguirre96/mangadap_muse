"""Methods for plotting DAP output.
"""


from __future__ import division, print_function, absolute_import

import sys

import numpy as np

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.ticker import MaxNLocator

from astropy.stats import sigma_clip

import warnings
try:
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        import seaborn as sns
except ImportError:
    print('Seaborn could not be imported. Continuing...')


def make_mask_no_measurement(data, err=None, val_no_measure=0.,
                             snr_thresh=1.):
    """Mask invalid measurements within a data array.

    Args:
        data (array): Data.
        err (array): Error. Defaults to None.
        val_no_measure (float): Value in data array that corresponds to no
            measurement.
        snr_thresh (float): Signal-to-noise threshold for keeping a valid
            measurement.

    Returns:
        array: Boolean array for mask (i.e., True corresponds to value to be
            masked out).
    """
    no_measure = (data == val_no_measure)
    if err is not None:
        no_measure[(err == 0.)] = True
        no_measure[(np.abs(data / err) < snr_thresh)] = True
    return no_measure


def make_mask_no_data(data, mask_no_measurement):
    """Mask entries with no data or invalid measurements.

    Args:
        data (array): Data.
        mask_no_measure (array): Mask for entries with no measurement.

    Returns:
        array: Boolean array for mask (i.e., True corresponds to value to be
            masked out).
    """
    no_data = np.isnan(data)
    no_data[mask_no_measurement] = True
    return no_data

def reorient(x):
    """Reorient XPOS and YPOS.

    XPOS and YPOS are oriented to start in the lower left and go up then
    right. Re-orient data so that it starts in the upper left and goes right
    then down.

    Args:
        x (array): Positional values.
        sqrtn (int): Square root of the number of spaxels.

    Returns:
        array: Square 2-D array of size (sqrtn, sqrtn).
    """
    sqrtn = int(np.sqrt(len(x)))
    x_sq = np.reshape(x, (sqrtn, sqrtn))
    return x_sq.T[::-1]

def set_extent(xpos, ypos, delta):
    """Set extent of map."""
    return np.array([-xpos.max() - delta, -xpos.min() + delta,
                    ypos.min() - delta, ypos.max() + delta])

def set_vmin_vmax(d, cbrange):
    """Set minimum and maximum values of the color map."""
    if 'vmin' not in d.keys():
        d['vmin'] = cbrange[0]
    if 'vmax' not in d.keys():
        d['vmax'] = cbrange[1]
    return d

def cbrange_sigclip(image, sigma):
    """Sigma clip colorbar range.

    Args:
        image (masked array): Image.
        sigma (float): Sigma to clip.

    Returns:
        list: Colorbar range.
    """
    imclip = sigma_clip(image.data[~image.mask], sig=sigma)
    try:
        cbrange = [imclip.min(), imclip.max()]
    except ValueError:
        cbrange = [image.min(), image.max()]
    return cbrange

def cbrange_user_defined(cbrange, cbrange_user):
    """Set user-specified colorbar range.

    Args:
        cbrange (list): Input colorbar range.
        cbrange_user (list): User-specified colorbar range. If a value is
            None, then the colorbar uses the previous value.

    Returns:
        list: Colorbar range.
    """
    for i in range(2):
        if cbrange_user[i] is not None:
            cbrange[i] = cbrange_user[i]
    return cbrange

def set_cbrange(image, cbrange=None, sigclip=None, symmetric=False):
    """Set colorbar range.

    Args:
        image (masked array): Image.
        cbrange (list): User-specified colorbar range. Defaults to None.
        sigclip (float): Sigma value for sigma clipping. If None, then do not
            clip. Defaults to None.
        symmetric (boolean): If True, make colorbar symmetric around zero.
            Defaults to False.

    Returns:
        list: Colorbar range.
    """

    if sigclip is not None:
        cbr = cbrange_sigclip(image, sigclip)
    else:
        cbr = [image.min(), image.max()]

    if cbrange is not None:
        cbr = cbrange_user_defined(cbr, cbrange)
    
    if symmetric:
        cb_max = np.max(np.abs(cbr))
        cbr = [-cb_max, cb_max]

    return cbr

def make_draw_colorbar_kws(image, cb_kws):
    """
    Args:
        image (masked array): Image to display.
        cb_kws (dict): Keyword args to set and draw colorbar.

    Returns:
        dict: draw_colorbar keyword args
    """
    keys = ('cbrange', 'sigclip', 'symmetric')
    cbrange_kws = {k: cb_kws.pop(k, None) for k in keys}
    cb_kws['cbrange'] = set_cbrange(image, **cbrange_kws)
    return cb_kws

def draw_colorbar(fig, p, axloc=None, cbrange=None, n_ticks=7, label_kws={},
                  tick_params_kws={}):

    if axloc is not None:
        cax = fig.add_axes(axloc)
    else:
        cax = None

    try:
        ticks = MaxNLocator(n_ticks).tick_values(*cbrange)
    except AttributeError:
        print('AttributeError: MaxNLocator instance has no attribute' +
              ' "tick_values" ')
        cb = fig.colorbar(p, cax)
    else:
        cb = fig.colorbar(p, cax, ticks=ticks)
    
    if label_kws['label'] is not None:
        cb.set_label(**label_kws)

    if tick_params_kws is not None:
        cb.ax.tick_params(**tick_params_kws)

    return fig, cb

def pretty_specind_units(units):
    """Convert units of spectral index to colorbar label.

    Args:
        units (str): 'ang' or 'mag'

    Returns:
        str
    """
    if units == 'ang':
        cblabel = r'$\AA$'
    elif units == 'mag':
        cblabel = 'Mag'
    else:
        raise('Unknown spectral index units.')
        cblabel = None
    return cblabel

def set_panel_par():
    ax_kws = dict(facecolor='#A8A8A8')
    imshow_kws = dict(cmap=cm.Blues_r) # cm.RdBu
    return ax_kws, imshow_kws

def set_single_panel_par():
    """Set default parameters for a single panel plot."""
    ax_kws, imshow_kws = set_panel_par()
    fig_kws = dict(figsize=(10, 8))
    title_kws = dict(fontsize=28)
    cb_kws = dict(axloc=[0.82, 0.1, 0.02, 5/6.],
                  cbrange=None,
                  sigclip=3, symmetric=False,
                  label_kws=dict(label=None, size=20),
                  tick_params_kws=dict(labelsize=20))
    return fig_kws, ax_kws, title_kws, imshow_kws, cb_kws

def set_multi_panel_par():
    """Set default parameters for a multi panel plot."""
    ax_kws, imshow_kws = set_panel_par()
    fig_kws = dict(figsize=(20, 12))
    title_kws = dict(fontsize=20)
    cb_kws = dict(cbrange=None,
                  sigclip=3, symmetric=False,
                  label_kws=dict(label=None, size=16),
                  tick_params_kws=dict(labelsize=16))
    return fig_kws, ax_kws, title_kws, imshow_kws, cb_kws


def make_image(val, err, xpos, ypos, binid, delta=0.25, val_no_measure=0,
               snr_thresh=1):
    """Make masked array of image.

    Args:
        val (array): Values.
        err (array): Errors.
        xpos (array): x-coordinates of bins.
        ypos (array): y-coordinates of bins.
        binid (array): Bin ID numbers.
        delta (float): Half of the spaxel size in arcsec.
        val_no_measure (float): Value that corresponds to no measurement.
        snr_thresh (float): Signal-to-noise theshold below which is not
           considered a measurement.

    Returns:
        tuple: (masked array of image,
                tuple of (x, y) coordinates of bins with no measurement)
    """
    # create a masked array of the data
    im = val[binid]
    im[binid == -1] = np.nan

    im_err = err[binid]
    im_err[binid == -1] = np.nan
    
    no_measure = make_mask_no_measurement(im, im_err)
    no_data = make_mask_no_data(im, no_measure)
    image = np.ma.array(im, mask=no_data)
    
    # spaxels with data but no measurement
    xpos_re = reorient(xpos)
    ypos_re = reorient(ypos)
    xy_nomeasure = (-(xpos_re[no_measure]+delta), ypos_re[no_measure]-delta)

    return image, xy_nomeasure



def ax_setup(fig=None, ax=None, fig_kws={}, facecolor='#EAEAF2'):
    """
    Args:
        fig: Matplotlib plt.figure object. Use if creating subplot of a
            multi-panel plot. Defaults to None.
        ax: Matplotlib plt.figure axis object. Use if creating subplot of a
            multi-panel plot. Defaults to None.
        fig_kws (dict): Keyword args to pass to plt.figure.
        facecolor (str): Axis facecolor. Defaults to '#EAEAF2'.

    Returns:
        fig: Matplotlib plt.figure object.
        ax: Matplotlib plt.figure axis object.
    """
    if 'seaborn' in sys.modules:
        if ax is None:
            sns.set_context('poster', rc={'lines.linewidth': 2})
        else:
            sns.set_context('talk', rc={'lines.linewidth': 2})
        sns.set_style(rc={'axes.facecolor': facecolor})

    if ax is None:
        fig = plt.figure(**fig_kws)
        ax = fig.add_axes([0.12, 0.1, 2/3., 5/6.])
        ax.set_xlabel('arcscec')
        ax.set_ylabel('arcsec')

    if 'seaborn' not in sys.modules:
        ax.set_axis_bgcolor(facecolor)

    ax.grid(False, which='both', axis='both')
    return fig, ax


def show_bin_num(binxrl, binyru, nbin, val, spaxel_size, ax, imshow_kws,
                 fontsize=6):
    """
    Args:
        binxrl (array):
        binyru (array):
        nbin (array):
        val (array): 
        spaxel_size (float):
        ax :
        image:
        fontsize (int): Nominal font size. Defaults to 6.
    
    Returns:
        axis object
    """
    for i, (x, y, nb, v) in enumerate(zip(binxrl, binyru, nbin, val)):
        fontsize_tmp = set_bin_num_fontsize(fontsize, i, nb)
        color = set_bin_num_color(v, imshow_kws)
        ax.text(-x, y, str(i), fontsize=fontsize_tmp, color=color,
                horizontalalignment='center', verticalalignment='center',
                zorder=10)
    return ax

def set_bin_num_color(value, imshow_kws):
    """

    Args:
        value (float): Map value for a bin.
        imshow_kws (dict): Keyword args passed to ax.imshow that create the
            colormap.

    Returns:
        tuple: Text color for bin number.
    """
    cmap, vmin, vmax = (imshow_kws[k] for k in ['cmap', 'vmin', 'vmax'])
    ctmp = cmap((value - vmin) / (vmax - vmin))
    color = (1.-ctmp[0], 1.-ctmp[1], 1.-ctmp[2], ctmp[3])  # invert
    return color

def set_bin_num_fontsize(fontsize, number, nbin):
    """Set font size of bin numbers.

    Args:
        fontsize (int): Nominal font size. Defaults to 7.
        number (int): Bin number.
        nbin (int): Number of bins 
    """
    if (number >= 10) and (number < 100) and (nbin <= 2):
        fontsize_out = fontsize - 2
    elif (number >= 100) and (number < 1000) and (nbin <= 2):
        fontsize_out = fontsize - 3
    elif (number >= 1000) and (nbin <= 2):
        fontsize_out = fontsize - 4
    elif nbin == 1:
        fontsize_out = fontsize
    else:
        fontsize_out = fontsize + 2
    return fontsize_out


def plot_map(image, extent, xy_nomeasure=None, fig=None, ax=None,
             fig_kws={}, ax_kws={}, title_kws={}, patch_kws={}, imshow_kws={},
             cb_kws={}, binnum_kws={}, bindot_args=()):
    """Plot map.

    Args:
        image (masked array): Image to display.
        extent (array): Minimum and maximum x- and y-values.
        xy_nomeasure (tuple): x- and y-coordinates of spaxels without
            measurements.
        fig: Matplotlib plt.figure object. Use if creating subplot of a
            multi-panel plot. Defaults to None.
        ax: Matplotlib plt.figure axis object. Use if creating subplot of a
            multi-panel plot. Defaults to None.
        fig_kws (dict): Keyword args to pass to plt.figure.
        ax_kws (dict): Keyword args to draw axis.
        title_kws (dict): Keyword args to pass to ax.set_title.
        patch_kws (dict): Keyword args to pass to ax.add_patch.
        imshow_kws (dict): Keyword args to pass to ax.imshow.
        cb_kws (dict): Keyword args to set and draw colorbar. 
        binnum_kws (dict): Keyword args to pass to show_bin_num.
        bindot_args (tuple): x- and y-coordinates of bins.

    Returns:
        tuple: (Matplotlib plt.figure object,
                Matplotlib plt.figure axis object)
    """
    
    fig, ax = ax_setup(fig, ax, fig_kws=fig_kws, **ax_kws)

    if title_kws['label'] is not None:
        ax.set_title(**title_kws)

    drawcb_kws = make_draw_colorbar_kws(image, cb_kws)
    imshow_kws = set_vmin_vmax(imshow_kws, drawcb_kws['cbrange'])

    # Plot regions with no measurement as hatched
    if xy_nomeasure is not None:
        for xh, yh in zip(*xy_nomeasure):
            ax.add_patch(mpl.patches.Rectangle((xh, yh), **patch_kws))

    p = ax.imshow(image, interpolation='none', extent=extent, **imshow_kws)

    fig, cb = draw_colorbar(fig, p, **drawcb_kws)

    if binnum_kws:
        ax = show_bin_num(ax=ax, **bin_kws)

    if bindot_args:
        ax.plot(*bindot_args, color='k', marker='.', markersize=3, ls='None',
                zorder=10)

    return fig, ax


def make_map_title(file_kws):
    return '     '.join(('pid-ifu {plate}-{ifudesign}', 'manga-id {mangaid}',
                        '{bintype}-{niter}')).format(**file_kws)


def make_big_axes(fig, axloc=[0.04, 0.05, 0.9, 0.88], xlabel=None, ylabel=None,
                  labelsize=20, title_kws={}):
    bigAxes = fig.add_axes(axloc, frameon=False)
    bigAxes.set_xticks([])
    bigAxes.set_yticks([])
    bigAxes.set_xlabel(xlabel, fontsize=labelsize)
    bigAxes.set_ylabel(ylabel, fontsize=labelsize)
    if title_kws:
        bigAxes.set_title(**title_kws)

def plot_multi_map(all_panel_kws, patch_kws={}, fig_kws={}, mg_kws={}):
    """
    Plot multiple maps at once.
    """
    fig = plt.figure(**fig_kws)
    if 'seaborn' in sys.modules:
        sns.set_context('poster', rc={'lines.linewidth': 2})

    bigtitle_kws = dict(fontsize=20)
    bigtitle_kws['label'] = make_map_title(mg_kws)
    bigAxes = make_big_axes(fig, xlabel='arcsec', ylabel='arcsec',
                            title_kws=bigtitle_kws)

    n_ax = len(all_panel_kws)
    for i, panel_kws in enumerate(all_panel_kws):
        dx = 0.31 * i
        dy = 0.45
        if i >= (n_ax / 2):
            dx = 0.31 * (i - n_ax / 2)
            dy = 0
        left, bottom = (0.08+dx, 0.1+dy)
        if 'seaborn' in sys.modules:
            sns.set_context('talk', rc={'lines.linewidth': 2})
            sns.set_style(rc={'axes.facecolor': '#A8A8A8'})

        ax = fig.add_axes([left, bottom, 0.2, 0.3333])
        panel_kws['cb_kws']['axloc'] = [left + 0.21, bottom, 0.01, 0.3333]

        if 'seaborn' not in sys.modules:
            ax.set_axis_bgcolor('#A8A8A8')
            ax.grid(False, which='both', axis='both')

        fig, ax = plot_map(fig=fig, ax=ax, patch_kws=patch_kws,
                           fig_kws=fig_kws, **panel_kws)


