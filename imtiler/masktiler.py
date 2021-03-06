from __future__ import absolute_import, print_function, division

from .util import *
from .basetiler import *
from numpy.random import randint, choice
        
class MaskTiler(BaseTiler):
    """
    MaskTiler(mask,tiledim,numtiles,maxsearch=1000,accept=0.5,reinit_seen=True,
              replacement=False,verbose=False)
    
    Summary: generates randomly selected tiledim x tiledim subtiles given
    initial mask of valid pixel locations
    
    Arguments:
    - mask: [nrows x ncols] bool mask indicating valid regions to sample in img
    - tiledim: tile dimension
    
    Keyword Arguments:
    - accept: max percentage of seen (mask==1) pixels/tile to accept
              (smaller values == less overlap)
    
    Output:
    - tileij = list of tiledim x tiledim tiles (2d slices) to use to extract subimages
    """

    def __init__(self,mask,tiledim,**kwargs):
        super(MaskTiler,self).__init__(tiledim,**kwargs)
        
        self.numtiles    = kwargs.pop('numtiles',MIN_TILES)
        self.maxsearch   = kwargs.pop('maxsearch',1000)
        self.replacement = kwargs.pop('replacement',False)
        self.reinit_seen = kwargs.pop('reinit_mask',True)
        self.accept      = kwargs.pop('accept',0.5)        
        self.verbose     = kwargs.pop('verbose',False)
        self.maxreinit   = kwargs.pop('maxreinit',10)
        self.exclude     = kwargs.pop('exclude_coords',[])
        
        nrows,ncols = mask.shape[0],mask.shape[1]         
        if nrows<tiledim or ncols<tiledim:
            msg='tiledim %d too large for shape (%d x %d)'%(tiledim,nrows,ncols)
            raise Exception(msg)

        self.tiles     = []
        self.nrows     = nrows
        self.ncols     = ncols
        self.tiledim   = tiledim
        self.ntilepix  = tiledim*tiledim

        # assign initial mask pixels + compute threshold
        self.maskskip  = np.uint32(mask==0) # 0=invalid pixel, so we should skip it
        self.maskseen  = self.maskskip.copy() # consider invalid pixels "seen"
        self.masksum   = self.maskskip.copy() # keep track of visits

        self.strict = False
        if self.accept=='none':
            # 'none' -> tile cannot contain any overlapping pixels
            self.accept = 0.0
            self.strict = True
        elif self.accept=='mask':
            self.accept = (1.25*self.maskskip.sum())/(nrows*ncols)
        elif self.accept=='min':
            # 'min' -> tiles can only contain 1 overlapping pixel
            self.accept = 2.0/self.ntilepix
        elif self.accept=='max':
            # 'max' -> tiles can contain all but 1 overlapping pixel
            self.accept = (self.ntilepix-2.0)/self.ntilepix
        else:
            self.accept = float(self.accept)
            if self.accept > 1:
                self.accept = self.accept/100.0

        self.accept = np.clip(self.accept,0.0,1.0)
        self.maxseen = int(self.accept*self.ntilepix)
        
        print('accept: %5.2f%%'%(self.accept*100))
        print('nmaskskip:',self.maskskip.sum(),'npix:',(nrows*ncols),
              'maxseen:',self.maxseen)                
                
        self.basestep = 1
        self.colstep = self.rowstep = self.basestep
        if self.nrows > self.ncols:
            self.rowstep *= int(np.ceil(self.nrows/self.ncols))
        elif self.nrows < self.ncols:
            self.colstep *= int(np.ceil(self.ncols/self.nrows))

        # get range of pixel offsets from tile dim
        self.rowdim    = self.nrows-(self.nrows%self.tiledim)
        self.coldim    = self.ncols-(self.ncols%self.tiledim)
        self.pixr      = blockpermute(np.arange(0,self.rowdim,self.rowstep))
        self.pixc      = blockpermute(np.arange(0,self.coldim,self.colstep))
        self.npixr     = len(self.pixr)
        self.npixc     = len(self.pixc)
        self.npixrc    = self.npixr*self.npixc
        #self.pixrc     = np.meshgrid(self.pixr,self.pixc)
        #self.pixrc     = np.c_[self.pixrc].reshape([2,-1]).T
        
        # compute number of rows/cols of tiledim-sized tiles
        self.ntilei    = int(np.ceil(self.nrows/tiledim))+1
        self.ntilej    = int(np.ceil(self.ncols/tiledim))+1
        self.ntileij   = self.ntilei*self.ntilej
        self.tilei     = np.arange(self.ntilei)
        self.tilej     = np.arange(self.ntilej)
        self.visited   = set([])
        #self.tileij    = np.meshgrid(tilei,tilej)
        #self.tileij    = np.c_[self.tileij].reshape([2,-1]).T

    def next(self):
        # randomly selects a tile from the list of pixel/tile offsets
        # while preserving state, allows for sampling with/without replacement
        # returns best tile slice and percent of unmasked pixels for best slice
        # (larger percentages=less overlap with previously-selected tiles)
        
        tijbest,tijseen,tijover = None,self.ntilepix,np.inf

        if len(self.pixr)==0 or len(self.tilei)==0:
            warn('no pixel offsets defined, cannot proceed')
            return (tijbest, tijseen)

        nreinit = 0
        nsearch = 0
        
        # pick a random row/col pixel offset from our seen pixel list
        #pixrc  = list(self.pixrc)
        #r,c = pixrc.pop(randint(len(pixrc)))
        r,c = choice(self.pixr),choice(self.pixc)
        while nsearch <= self.maxsearch:
            #tilei,tilej = list(self.tilei),list(self.tilej)
            # search tiles in random order for current pixel offset
            for itileij in range(self.ntileij):
                if len(self.visited)==self.ntileij*self.npixrc:
                    self.visited = set([])                
                #ti,tj = tileij.pop(randint(len(tileij)))
                ti,tj = choice(self.tilei),choice(self.tilej)
                i,j = (ti*self.tiledim)+r,(tj*self.tiledim)+c
                if (i,j) in self.visited or i+self.tiledim>=self.nrows or \
                   j+self.tiledim>=self.ncols:
                    #  TODO (BDB, 02/21/17): allow padding here? 
                    continue
                else:
                    self.visited.add((i,j))

                tij = (slice(i,i+self.tiledim,None),
                       slice(j,j+self.tiledim,None))
                
                # select tile with the fewest seen (maskseen==1) pixels
                tvals = self.maskseen[tij]
                nseen = np.count_nonzero(tvals)
                if nseen<tijseen or self.replacement:
                    nover = self.masksum[tij].max()
                    if nover<=tijover:
                        tijbest, tijseen, tijover = tij, nseen, nover
                        if nseen<=self.maxseen:
                            # exit early if we meet stopping criteria
                            nsearch = self.maxsearch
                            break

            # found an acceptable tile or all pixels masked inseen (reset or exit)
            if nsearch>=self.maxsearch:                                                
                # reset mask if our best tile is covered by more than maxseen
                if self.reinit_seen and tijseen>self.maxseen:
                    if self.verbose:
                        tcoverage = tijseen/self.ntilepix
                        msg = "Reinitializing mask (%6.3f%% coverage)"%tcoverage
                        print(msg)
                    self.maskseen = self.maskskip.copy()
                    # pick a new offset to increase sampling diversity
                    r,c = choice(self.pixr),choice(self.pixc)

                    tijbest,tijseen,tijover = None,self.ntilepix,np.inf
                    nsearch = 0
                    nreinit += 1
                    if nreinit > self.maxreinit:
                        # bail out if we have no choice
                        break
                else:
                    # found a good tile, mask if sampling wo replacement
                    if not self.replacement:
                        self.maskseen[tijbest] += 1
                        self.masksum[tijbest] += 1
                    nreinit = 0 # we can reinit again if we found a good tile
                    break

            # randomly increment either the row or the column, but not both
            if randint(2)==1:
                r = (r+choice(self.tilei))%self.rowdim
            else:
                c = (c+choice(self.tilej))%self.coldim
                
            # keep track of searches to avoid infinite loop
            nsearch += 1
                    
        return (tijbest, tijseen)
    
    @timeit
    def collect(self):
        if self.ul != []:
            return self.ul

        ul = []
        tiles = []
        percent_seen = []

        if self.verbose:
            nrows,ncols = self.maskskip.shape[:2]
            print('Collecting up to',self.numtiles,'tiles')
            print('Image dims: (%d x %d)'%(nrows,ncols))
            print('Tile dims: (%d x %d)'%(self.tiledim,self.tiledim))
            
        for i in range(self.numtiles):
            tij,tijseen = self.next()
            if tij==None:
                break
            tijpercent = tijseen/self.ntilepix
            if self.verbose:
                print(i,tile2str(tij),'%5.4f'%tijpercent)

            # if strict mode on, discard 'best match' tiles below criteria
            if self.strict and tijseen > self.maxseen:
                if self.verbose:
                    print('skipped %d: tijseen=%d > maxseen=%d'%(i,tijseen,
                                                                 self.maxseen))
                continue
            tiles.append(tij)
            ul.append((tij[0].start,tij[1].start))
            percent_seen.append(tijpercent)

        self.tiles = tiles
        self.percent_seen = percent_seen
        numtiles = len(tiles)
        print('Collected',numtiles,'of',self.numtiles,'requested tiles')
        self.numtiles = numtiles
        self.ul = list(set(ul))
        return self.ul
