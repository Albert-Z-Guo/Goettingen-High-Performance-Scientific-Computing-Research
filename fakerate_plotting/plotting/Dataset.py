"""@package Dataset
Helper class to store datasets and related quantities

@author Christian Grefe, Bonn University (christian.grefe@cern.ch)
"""

from plotting.BasicPlot import BasicPlot
from plotting.TreePlot import getValuesFromTree, create2DHistogramFromTree
from plotting.AtlasStyle import Style
from plotting.Cut import Cut
from plotting.HistogramStore import HistogramStore
from plotting.Tools import string2bool, overflowIntoLastBins, progressBarInt
from plotting.Variable import createCutFlowVariable, VariableBinning, var_Yield
from plotting.Systematics import SystematicsSet, TreeSystematicVariation
from plotting.CrossSectionDB import CrossSectionDB
from plotting import DistributionTools
import plotting.Tools as Tools
from copy import copy
import logging, re, os, uuid, math, hashlib

def findAllFilesInPath( pattern ):
    ## helper method to resolve regular expressions in file names
    #  TChain::Add only supports wildcards in the last items, i.e. on file level.
    #  This method can resolve all wildcards at any directory level, e.g. /my/directory/a*test*/pattern/*.root
    #  @param pattern      the file name pattern using vaild python reg expressions
    #  @return list of all files matching the pattern 
    files = []
    path = ''
    items = pattern.split( '/' )
    
    def checkPath( path, items ):
        ## helper method to deal with the recursion 
        import ROOT
        if not items:
            return
        myItems = copy( items )
        item = myItems.pop(0)
        if '*' in item:
            directory = ROOT.gSystem.OpenDirectory( path )
            #DR item = item.replace( '*', '.*' )
            # DR beg and end of line control so that *truc does not match bla_truc_xyz
            item = "^"+item.replace( '*', '.*' )+"$" 
            p = re.compile( item )
            entry = True
            while entry:
                entry = ROOT.gSystem.GetDirEntry( directory )
                if p.match( entry ):
                    if not myItems:
                        files.append( path + entry )
                    else:
                        checkPath( path + entry + '/', myItems)
            ROOT.gSystem.FreeDirectory( directory )
        elif item and not myItems:
            files.append( path + item )
        else:
            checkPath( path + item + '/', myItems )
        
    checkPath( path, items )
    return files

def extractHistogramsFromRootDirectory( directory ):
    ## Helper method to collect all histogram type objects from a TDirectory, ie. a ROOT file
    #  @param directory     TDirectory object which is scanned recursively
    #  @return dictionary mapping path to histogram object
    from ROOT import gROOT, TDirectory, TH1
    histograms = {}
    directoryPath = directory.GetPath().split(':')[1]
    for key in directory.GetListOfKeys():
        className = key.GetClassName()
        classObj = gROOT.GetClass( className )
        keyName = key.GetName() 
        if classObj.InheritsFrom( TDirectory.Class() ):
            histograms.update( extractHistogramsFromRootDirectory( directory.Get( keyName ) ) )
        elif classObj.InheritsFrom( TH1.Class() ):
            histograms[ os.path.join( directoryPath, keyName ) ] = directory.Get( keyName )
        else:
            pass
    return histograms

def getDatasetHistogramBinContent( dataset, histogramName, binIndex ):
    ## Helper method to calculate the bin content of a given histogram
    #  bin from all input files in the given dataset
    #  @param dataset           input dataset
    #  @param histogramName     name of the histogram
    #  @param binIndex          index of the bin
    #  @result the total bin content over all files in the dataset 
    result = 0
    from ROOT import TFile
    for fileNamePattern in dataset.fileNames:
        for fileName in findAllFilesInPath( fileNamePattern ):
            rootFile = TFile.Open( fileName )
            if rootFile and rootFile.IsOpen():
                h = rootFile.Get( histogramName )
            if h:
                result += h.GetBinContent( binIndex )
            rootFile.Close()
    return result

class SumOfWeightsCalculator( object ):
    ## Helper class to wrap sum of weight calculation
    
    def __init__( self ):
        ## Default constructor
        self.sumOfWeights = 0.
        self.calculated = False
    
    def calculate( self, dataset ):
        ## Calculate the sum of weights for the given dataset
        #  @param dataset     input dataset
        #  @return the sum of weights 
        return 0
    

class HistogramBasedSumOfWeightsCalculator( SumOfWeightsCalculator ):
    ## Helper class to wrap sum of weight calculation
    
    def __init__( self, histogramName='h_metadata', binIndex=7 ):
        ## Default constructor
        SumOfWeightsCalculator.__init__( self )
        self.histogramName = histogramName
        self.binIndex = int(binIndex)
    
    def calculate( self, dataset ):
        ## Calculate the sum of weights for the given dataset
        #  @param dataset     input dataset
        #  @return the sum of weights
        if self.calculated:
            dataset.logger.debug( 'HistogramBasedSumOfWeightsCalculator(): metadatahist=%s , binIndex=%g, sum of weights= %g' % ( self.histogramName,self.binIndex,self.sumOfWeights ) )
            return self.sumOfWeights
        self.sumOfWeights = getDatasetHistogramBinContent( dataset, self.histogramName, self.binIndex )
        # protect for floating point precision to avoid tiny sum of weights
        if abs(self.sumOfWeights) < 1e-09:
            self.sumOfWeights = 0
        dataset.logger.debug( 'HistogramBasedSumOfWeightsCalculator(): metadatahist=%s , binIndex=%g, sum of weights= %g' % ( self.histogramName,self.binIndex,self.sumOfWeights ) )
        return self.sumOfWeights
    
class FriendTree( object ):
    ## container class to store friend tree
    logger = logging.getLogger( __name__ + '.FriendTree' )
    
    def __init__( self, treeName, fileNames, alias='', systematicVariations=[] ):
        ## Default constructor
        #  @param fileNames        list of fileNames
        #  @param treeName         name of the friend tree in the files
        #  @param alias            alias of the friend tree (use in case of clash with main tree)
        #  @systematicVariation    define for which tree systematics this friend tree should be used (empty list is used for all)
        self.fileNames = fileNames
        self.treeName = treeName
        self.alias = alias
        self.systematicVariations = systematicVariations
        self.tree = None
        
    def _open( self ):
        ## Helper method to open all files
 #       import os
 #       os.system("lsof | grep '/Users/Eric/runII/hadhad/plots/v17/170522_testZttDecor/decorationsZtt.root' | wc ")
 #       print "Opening friendTree", self.treeName,self.fileNames
        from ROOT import TChain
        self.tree = TChain( self.treeName )
        nFiles = 0
        for fileNamePattern in self.fileNames:
            for fileName in findAllFilesInPath( fileNamePattern ):
                nFiles += self.tree.Add( fileName )
        if nFiles:
            # update the estimate for this TChain, needed for proper use of GetSelectedRows
            self.tree.SetEstimate( self.tree.GetEntries() + 1 )
            self.logger.debug( '_open(): opened %d files with %d entries from %r' % ( nFiles, self.tree.GetEntries(), self.fileNames ) )
        else:
            self.logger.warning( '_open(): found no files for %r in %r' % ( self, self.fileNames ) )
    
    def _close( self ):
        ## Delete the link to the tree allowing the underlying file to be closed
        #  The file might still open if other trees or other references to this tree are stored elsewhere
        if self.tree:
            del self.tree
    
    def addTo( self, tree ):
        ## Add this tree as a friend to the given tree
        treeName = tree.GetName()
        # check if this tree is a suitable friend for this systematic variation
        if self.systematicVariations:
            use = False
            for variation in self.systematicVariations:
                if treeName == variation.treeName:
                    use = True
                    break
            if not use:
                return
        if not self.tree:
            self._open()
        if not self.tree:
            return
        self.logger.debug( '_addTo(): adding "%s" as friend tree to "%s"' % ( self.treeName, treeName ) )
        tree.AddFriend( self.tree, self.alias )
        
# store all available datasets
DATASETS={}   

class Dataset( object ):
    ## Container class for a dataset and associated information. Datasets are opened as TChains
    defaultHistogramStore = None
    defaultSumOfEventsCalculator = HistogramBasedSumOfWeightsCalculator( 'h_metadata', 7 )
    defaultSumOfWeightsCalculator = HistogramBasedSumOfWeightsCalculator( 'h_metadata', 8 )
    defaultSumOfWeightsSquaredCalculator = HistogramBasedSumOfWeightsCalculator( 'h_metadata', 9 )
    logger = logging.getLogger( __name__ + '.Dataset' )
    
    def __init__( self, name, title='',fileNames=[], treeName='NOMINAL', style=None, weightExpression='', crossSection=1., kFactor=1., isData=False, isSignal=False, isBSMSignal=False,titleLatex=''):
        ## Default contructor
        #  @param name               name of the dataset used for output file names
        #  @param title              title used for example in legend entries (use TLatex here)
        #  @param fileNames          list of input file names belonging to this dataset
        #  @param treeName           name of the ROOT tree in each file
        #  @param style              default Style object associated with this dataset
        #  @param weight             default weight expression for this dataset
        #  @param crossSection       sets the crossSection (in pb)
        #  @param kFactor            correction factor applied as scaling to all histograms
        #  @param isData             this is data (not MC) no scale factors will be applied to histograms
        #  @param isSignal           this is signal MC, simply stored to decide how it is used in MVA training
        #  @param isBSMSignal        this is BSM signal MC (can be useful to separate from SM signal)
        self.openTrees = {}
        self.keepTreesInMemory = False
        self.treeEntryLists = {}
        self.name = name
        self.scaleFactors = {}
        self.scaleFactorsUncertainty = {}
        self.title = title if title else name
        self.titleLatex= titleLatex if titleLatex else title.replace("#","\\")
        self.style = style 
        self.weightExpression = weightExpression
        self.sumOfWeights = 0.
        self.crossSection = crossSection
        self.kFactor = kFactor
        self.fileNames = fileNames if fileNames else []
        self.friendTrees = []
        self.isData = isData
        self.isSignal = isSignal
        self.isBSMSignal = isBSMSignal
        self.ignoreCuts = []
        self.addCuts = []
        self.replaceVariables = {}
        self.histogramStore = self.defaultHistogramStore
        self.sumOfEventsCalculator = copy( self.defaultSumOfEventsCalculator )
        self.sumOfWeightsCalculator = copy( self.defaultSumOfWeightsCalculator )
        self.sumOfWeightsSquaredCalculator = copy( self.defaultSumOfWeightsSquaredCalculator )
        self.systematicsSet = SystematicsSet()
        self.nominalSystematics = TreeSystematicVariation( 'nominal', 'Nominal', treeName )
        self.preselection = Cut()
        self.metadata = None
        
        # internals
        self._dsid = 0
        self._hashFileNames = None
        
    def copy( self, name, title):
        ## create a copy of this dataset with the given name and title
        #  @param name               name of the dataset copy used for output file names
        #  @param title              title used for example in legend entries (use TLatex here)
        #  @return the copied dataset
        dataset = copy( self )
        dataset.name = name
        dataset.title = title
        # explicit copies of contained lists and dictionaries to avoid clashes
        dataset.openTrees = self.openTrees.copy()
        dataset.friendTrees = copy(self.friendTrees)
        dataset.scaleFactors = self.scaleFactors.copy()
        dataset.fileNames = copy(self.fileNames)
        dataset.ignoreCuts = copy(self.ignoreCuts)
        dataset.addCuts = copy(self.addCuts)
        dataset.systematicsSet = copy(self.systematicsSet)
        return dataset

    @classmethod
    def fromString( cls, s ):
        ## Contructor from string used to read in text files
        #  Format is "name; title; treeName; color; crossSection; kFactor; fileName1, fileName2, ... "
        result = [ x.lstrip().rstrip() for x in s.split( ';' ) ]
        name = result[0] if len(result) > 0 else 'DataSet'
        title = result[1] if len(result) > 1 else None
        treeName = result[2] if len(result) > 2 else 'NOMINAL'
        lineColor = int(result[3]) if len(result) > 3 else 0
        crossSection = float(result[4]) if len(result) > 4 else 1.
        kFactor = float(result[5]) if len(result) > 5 else 1.
        fileNamesString = result[6] if len(result) > 6 else ''
        fileNames = [ x.strip() for x in fileNamesString.split( ',' ) ]
        return cls( name, title, fileNames, treeName, Style(lineColor), crossSection=crossSection, kFactor=kFactor )

    @classmethod
    def fromXML( cls, element ):
        ## Constructor from an XML element
        #  <Dataset name="" title="" treeName="" isData="" isSignal="" crossSection="" kFactor="" dsid="">
        #    <Style color="5"/>
        #    <File> File1 </File>
        #    <File> File2 </File>
        #    <AddCuts>
        #      <Cut> Cut1 </Cut>
        #      <Cut> Cut2 </Cut>
        #    </AddCuts>
        #    <IgnoreCuts>
        #      <Cut> Cut3 </Cut>
        #      <Cut> Cut4 </Cut>
        #    </IgnoreCuts>
        #  </Dataset>
        #  @param element    the XML element
        #  @return the HistogramStore object
        attributes = element.attrib
        name = attributes[ 'name' ]
        if DATASETS.has_key( name ):
            return DATASETS[name]
        dataset = cls( name )
        if attributes.has_key( 'title' ):
            dataset.title = attributes['title']
        if attributes.has_key( 'treeName' ):
            dataset.treeName = attributes['treeName']
        if attributes.has_key( 'isData' ):
            dataset.isData = string2bool(attributes['isData'])
        if attributes.has_key( 'isSignal' ):
            dataset.isSignal = string2bool(attributes['isSignal'])
        if attributes.has_key( 'isBSMSignal' ):
            dataset.isBSMSignal = string2bool(attributes['isBSMSignal'])
        if attributes.has_key( 'crossSection' ):
            dataset.crossSection = float(attributes['crossSection'])
        if attributes.has_key( 'kFactor' ):
            dataset.kFactor = float(attributes['kFactor'])
        if attributes.has_key( 'weightExpression' ):
            dataset.weightExpression = attributes['weightExpression']
        if attributes.has_key( 'dsid' ):
            dataset.dsid = int( attributes['dsid'] )
        dataset.style = Style.fromXML( element.find( 'Style' ) ) if element.find( 'Style' ) is not None else None
        for fileElement in element.findall( 'File' ):
            dataset.fileNames.append( fileElement.text.strip() )
        if element.find( 'AddCuts' ):
            for cutElement in element.find( 'AddCuts' ).findall( 'Cut' ):
                dataset.addCuts.append( Cut.fromXML( cutElement ) )
        if element.find( 'IgnoreCuts' ):
            for cutElement in element.find( 'IgnoreCuts' ).findall( 'Cut' ):
                dataset.ignoreCuts.append( Cut.fromXML( cutElement ) )
        return dataset
        
    def __repr__( self ):
        return 'Dataset(%s)' % self.name
    
    def __str__( self ):
        return 'Dataset(%s): XS=%g pb, effXS=%g pb, sF=%r' % (self.name, self.crossSection, self.effectiveCrossSection, self.scaleFactors)
    
    def _calculateHash( self ):
        ## hash value calculated from file size, first and last MB of all input files and the sum of weights
        # first check if the list of file names has changed
        if self._hashFileNames == self.fileNames:
            return
        defaultCheckSize = 2**13    # 1024 bytes
        md5 = hashlib.md5()
        for fileNamePattern in self.fileNames:
            for fileName in findAllFilesInPath( fileNamePattern ):
                fileSize = os.path.getsize( fileName )
                checkSize = defaultCheckSize if fileSize > defaultCheckSize else fileSize
                with open( fileName , "rb" ) as f:
                    # read the first MB
                    md5.update( f.read( checkSize ) )
                    # go to 1 MB before the end
                    f.seek( -checkSize, 2 )
                    # read last MB
                    md5.update( f.read( checkSize ) )
                # include the file size in the hash
                md5.update( str(fileSize) )
        self._open()
        # include the sum of weights in the hash
        md5.update( str(self.sumOfWeights) )
        # store the hash for later use
        self._hash = md5
        # store the list of file names used to generate the hash
        self._hashFileNames = self.fileNames
    
    @property
    def md5( self ):
        self._calculateHash()
        h = self._hash.copy()
        # include the weight expression
        if self.weightExpression:
            h.update( self.weightExpression )
        return h.hexdigest()

    def _open( self, treeName=None ):
        ## Read the files into the TChain. Automatically called when trying to create a histogram
        if not treeName:
            treeName = self.nominalSystematics.treeName
        if self.openTrees.has_key( treeName ):
            return self.openTrees[ treeName ]
        from ROOT import TChain
        tree = TChain( treeName )
        nFiles = 0
        for fileNamePattern in self.fileNames:
            for fileName in findAllFilesInPath( fileNamePattern ):
                nFiles += tree.Add( fileName )
        if nFiles>0:
            # update the estimate for this TChain, needed for proper use of GetSelectedRows
            tree.SetEstimate( tree.GetEntries() + 1 )
            if self.keepTreesInMemory:
                self.openTrees[ treeName ] = tree
            self._applyPreselectionToTree( tree )
            self.logger.debug( '_open(): opened %d files with %d entries from %r with sum of weights %g' % ( nFiles, tree.GetEntries(), self.fileNames, self.sumOfWeights ) )
            # add friend trees
            for friend in self.friendTrees:
                self.logger.debug( '_open(): add friend %s to %s' % ( friend.alias,self.name ) )
                friend.addTo( tree )
        else:
            self.logger.warning( '_open(): found no files for %r in %r' % ( self, self.fileNames ) )
        return tree
    
    def _close( self, treeName ):
        if self.openTrees.has_key( treeName ):
            del self.openTrees[ treeName ]
        for fTree in self.friendTrees:
          if fTree.tree:
            fTree.tree.GetCurrentFile().Close()
          fTree.tree=None
          #del fTree

        
    def _applyPreselectionToTree( self, tree ):
        ## helper method to apply preselection using TEntryList
        treeName = tree.GetName()
        selection = self.preselection.cut
        listName = 'entryList_%s_%s' % ( treeName, self.name ) 
        entryList = None
        if tree.GetEntryList():
            # we already had set an entry list, replace with NULL
            tree.SetEntryList( 0 )
            self.logger.debug( '_applyPreselectionToTree(): removing preselection for %s in %r' % ( tree, self ) )
        if not selection:
            # nothing to select simply return
            return
        if self.treeEntryLists.has_key( treeName ):
            entryList = self.treeEntryLists[ treeName ]
        else:
            tree.Draw( '>>' + listName, selection, 'entrylist' )
            from ROOT import gDirectory
            entryList = gDirectory.Get( listName )
            self.treeEntryLists[ treeName ] = entryList
        if entryList:
            tree.SetEntryList( entryList )
    
    def _determineVariable( self, variable ):
        ## helper method to determine the final variable to use
        if self.replaceVariables.has_key( variable ):
            return self.replaceVariables[ variable ]
        return variable
            
    def _determineCut( self, cut ):
        ## helper method to determine the cut to use
        cut = Cut() + cut
        for ignoredCut in self.ignoreCuts:
            cut = cut.withoutCut( ignoredCut )
        for addedCut in self.addCuts:
            cut += addedCut
        return cut
    
    def _register( self, oldName='' ):
        if oldName and DATASETS.has_key( oldName ) and DATASETS[ oldName ] == self:
            del DATASETS[ oldName ]
        if DATASETS.has_key( self.name ) and  DATASETS[ self.name ] is not self:
            self.logger.warning( 'name(): registering a Dataset with an already existing name: "%s"' % self.name )
        DATASETS[ self.name ] = self
    
    @property
    def name( self ):
        ## Get the dataset name
        return self._name
    
    @name.setter
    def name( self, name ):
        ## Set the dataset name
        oldName = None
        try:
            oldName = self.name
        except AttributeError:
            oldName = None
        self._name = name
        if name is not oldName:
            self._register( oldName )
    
    @property
    def combinedScaleFactors( self ):
        ## get the product of all global scale factors
        product = 1.0
        for value in self.scaleFactors.itervalues():
            product *= value
        return product
    
    @property 
    def crossSection( self ):
        ## Get the cross section in pb
        return self.__crossSection
    
    @crossSection.setter 
    def crossSection( self, value ):
        ## Set the cross section in pb
        self.scaleFactors['crossSection'] = value
        self.__crossSection = value
        
    @property 
    def kFactor( self ):
        ## Get the kFactor
        return self.__kFactor
    
    @kFactor.setter 
    def kFactor( self, value ):
        ## Set the kFactor
        self.scaleFactors['kFactor'] = value
        self.__kFactor = value
    
    @property
    def combinedSystematicsSet( self ):
        ## Get the SystematicsSet
        return self.systematicsSet
       
    @property 
    def systematicsSet( self ):
        ## Get the SystematicsSet
        return self.__systematicsSet
    
    @systematicsSet.setter 
    def systematicsSet( self, value ):
        ## Set the SystematicsSet
        self.__systematicsSet = value
    
    @property
    def effectiveCrossSection( self ):
        ## Get the effective cross section in pb including scale factors
        return self.combinedScaleFactors * self.systematicsSet.totalScaleFactor()
    
    @property
    def weightExpression( self ):
        ## Get the weight expression applied to this Dataset
        return self.__weightExpression
    
    @weightExpression.setter
    def weightExpression( self, expression ):
        ## Set the weight expression
        self.__weightExpression = expression
    
    @property
    def sumOfWeights( self ):
        ## Get the total sum of weights of this dataset. Used to take into account cut efficiencies correctly
        #  Unless set it is calculated using the sumOfWeightsCalculator.
        if self.__sumOfWeights:
            return self.__sumOfWeights
        else:
            return self.sumOfWeightsCalculator.calculate( self )
    
    @sumOfWeights.setter
    def sumOfWeights( self, expression ):
        ## Set the total sum of weights
        self.__sumOfWeights = expression
    
    @property
    def entries( self ):
        ## Get the number of entries in the dataset
        tree = self._open( self.nominalSystematics.treeName )
        return tree.GetEntries()
    
    @property
    def trueDatasets( self ):
        ## Get list of all contained datasets
        #  Recursively resolves all PhysicsProcess daughters, only returns Dataset objects
        return [self]
    
    @property
    def preselection( self ):
        ## Get the preselection cut currently applied
        return self._preselection
    
    @preselection.setter
    def preselection( self, cut=Cut() ):
        ## Define a preselection for this dataset using the TEntryList functionality of TTree
        #  Use an empty cut to reset the preselection
        #  WARNING: this selection is always active even if a looser selection is drawn
        #  @param cut    the preselection cut to apply
        self._preselection = cut
        # remove all stored TEntryLists
        self.treeEntryLists.clear()
        # apply the preselection to all open trees
        for tree in self.openTrees.itervalues():
            self._applyPreselectionToTree( tree )
    
    @property
    def dsid( self ):
        ## Get the DSID
        return self._dsid
    
    @dsid.setter
    def dsid( self, dsid ):
        ## Set the DSID and update cross section, k-factor and filter efficiency from DB
        self._dsid = dsid
        if dsid:
            db = CrossSectionDB.get()
            try:
                dbEntry = db[ dsid ]
                self.crossSection = dbEntry.crossSection
                self.kFactor = dbEntry.kFactor
                self.scaleFactors['branchingRatio'] = dbEntry.branchingRatio
                self.scaleFactors['filterEfficiency'] = dbEntry.efficiency
            except KeyError:
                self.logger.warning( 'dsid(): unable to find cross section for DSID %d' % dsid )
    
    def addSystematics( self, systematics ):
        ## Add a Systematics object to this Dataset
        try:
            self.systematicsSet |= systematics
        except TypeError:
            self.systematicsSet.add( systematics )
            
    def addFriendTree( self, friendTree ):
        ## Add a FriendTree object to this dataset
        self.friendTrees.append( friendTree )
        
    def removeSystematics( self, systematics ):
        ## Remove a Systematics object from this Dataset
        self.systematicsSet.discard( systematics )
        
    def addSystematicsToAllDaughters( self, systematics ):
        ## Add a Systematics object to all daughter Datasets
        self.addSystematics( systematics )
        
    def removeSystematicsFromAllDaughters( self, systematics ):
        ## Remove a Systematics object from this Dataset
        self.removeSystematics( systematics )
    
    def addFriendTreeToAllDaughters( self, friendTree ):
        ## Add a FriendTree object to all contained datasets
        self.addFriendTree( friendTree )
    
    def save( self, fileName, selection=None ):
        ## Store this dataset into a single ROOT file. The given selection is applied.
        #  All trees registered in the SystematicsSet are stored. In addition, all histogram objects found
        #  are added up and stored in the output file.
        #  @param fileName      name of the output file
        #  @param selection     event selection applied to the trees (default preselection if defined)
        if selection is None:
            selection = self.preselection.cut if self.preselection else ''
        from ROOT import TFile
        outputFile = TFile.Open( fileName, 'RECREATE' )
        if not outputFile or not outputFile.IsOpen():
            self.logger.error( 'save(): unable to open output file at "%s"' % fileName )
            return
        # copy all trees connected to any systematics
        treeNames = self.systematicsSet.treeNames
        nTrees = len( treeNames ) + 1
        self.logger.info( 'save(): storing %d trees with selection="%s" in %s' % ( nTrees, selection, fileName ) )
        for index, treeName in enumerate( [self.nominalSystematics.treeName] + sorted( treeNames ) ):
            tree = self._open( treeName )
            if not tree.GetEntries():
                continue
            outputFile.cd()
            progressBarInt( index, nTrees, 'Writing: ' + treeName )
            if tree.GetEntries():
                newtree = tree.CopyTree( selection )
            else:
                from ROOT import TTree
                newtree = TTree( treeName, treeName )
            newtree.Write()
            self.logger.debug( 'save(): selected %d/%d entries from %s' % ( newtree.GetEntries(), tree.GetEntries(), treeName ) )
        progressBarInt( nTrees, nTrees, 'Done' )
        # now get all histogram objects and add them together
        histograms = {}
        for fileNamePattern in self.fileNames:
            for fileName in findAllFilesInPath( fileNamePattern ):
                rootFile = TFile.Open( fileName )
                if rootFile and rootFile.IsOpen():
                    for path, hist in extractHistogramsFromRootDirectory( rootFile ).items():
                        if histograms.has_key( path ):
                            histograms[path].Add( hist )
                        else:
                            outputFile.cd()
                            histograms[path] = hist.Clone( '%s_%s' % (hist.GetName(), uuid.uuid1() ) )
                    rootFile.Close()
        # store the histograms in the output file
        self.logger.debug( 'save(): storing %d histograms' % ( len( histograms ) ) )
        for path in sorted( histograms.keys() ):
            hist = histograms[path]
            outputFile.cd()
            path, name = os.path.split( path )
            if not outputFile.GetDirectory( path ):
                outputFile.mkdir( path )
            outputFile.cd( path )
            hist.Write( name )
        outputFile.Close()
        
    def addToTmvaFactory( self, factory, cut=Cut(), weightExpression=None, luminosity=1., className='Background', tmvaWeightBranch='TmvaWeight', systematicsSet=None, scaleFactor=1. ):
        ## Add the tree of this dataset to a TMVA factory
        #  An in memory copy of the tree is created with the selection cut already applied.
        #  Then a branch with the combined event weight is computed and the tree is added to the TMVA::Factory object
        #  Events with negative weight are kept with positive weight instead to reatin them for increased training statistics
        #  @param factory              TMVA.Factory object to which this tree will be added
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data 
        #  @param className            class used in the classification (usually "Signal" or "Background")
        #  @param systematicsSet       additional systematics that should be considered
        #  @param scaleFactpr          additional scale factor that should be considered
        from ROOT import TTree, TTreeFormula
        tree = self._open( self.nominalSystematics.treeName )
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        weightExpression = weightExpression if weightExpression else self.weightExpression
        weightExpression *= systematicsSet.totalWeight( cut=cut )
        cut = self._determineCut( cut )
        # copy the relevant entries into a new tree
        nEntries=tree.GetEntries()
        copyTree = tree.CopyTree( cut.cut,"",nEntries )
        # remove tree ownership from current directory
        copyTree.SetDirectory( 0 )
        # create a branch to store the combined event weights
        from array import array
        # must be zero for cross evaluation
        weightValue = array( 'f', [0.] )
        weightBranch = copyTree.Branch( tmvaWeightBranch, weightValue, tmvaWeightBranch+'/F' )
        # suppress some standard ROOT warnings
        import warnings
        warnings.filterwarnings( action='ignore', category=RuntimeWarning, message='creating converter.*' )
        # create the TTreeFormula to evaluate the weight expression
        weightFormula = weightExpression.getTTreeFormula( copyTree )
        copyTree.SetNotify(weightFormula)
        for i in range(copyTree.GetEntries()):
            copyTree.GetEntry(i)
            weightValue[0] = weightFormula.EvalInstance()
            weightBranch.Fill()
        self.logger.info( 'addToTMVAFactory: Adding Tree from DS %s' % ( self.name ) )
        
        if copyTree.GetEntries() > 0:
            scaleFactor *= self.combinedScaleFactors * systematicsSet.totalScaleFactor( cut=cut ) / self.sumOfWeights
            if not self.isData:
                scaleFactor *= luminosity
            factory.AddTree( copyTree, className, scaleFactor, cut.getTCut() )
        
    def getYieldSlow( self, cut=Cut(), weightExpression=None, luminosity=1., ignoreWeights=False, systematicVariation=None, ignoreDataWeight=False, ignoreSF=False, systematicsSet=None ):
        ## Calculate the expected yield for the given selection
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param ignoreDataWeight     used for fake-factor data-mc where weight is to be applied to data via self.weightExpression
        #  @param ignoreWeights        ignore the weights and any other scale factors including luminosity
        #  @param ignoreSF             ignore scalefactors but don't ignore other weights
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param systematicsSet       additional systematics that should be considered
        #  @return the (yield, uncertainty)
        systematicVariation = systematicVariation if systematicVariation else self.nominalSystematics
        tree = self._open( systematicVariation.treeName )
        if not tree:
            return
        weightExpression = weightExpression if weightExpression else self.weightExpression
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        if ignoreDataWeight and self.isData:
            weightExpression = self.weightExpression
        if ignoreWeights: 
            selection = cut.cut
        elif ignoreSF:
            selection = ( self._determineCut(cut) * weightExpression ).cut
        else: 
            selection= ( self._determineCut(cut) * weightExpression * systematicsSet.totalWeight( systematicVariation ) ).cut
        
        expression = selection
        if ignoreWeights or ignoreSF:
            scaleFactor = 1
        else:
            scaleFactor = self.combinedScaleFactors * systematicsSet.totalScaleFactor( systematicVariation, cut )
            if luminosity and not self.isData:
                scaleFactor *= luminosity
            if self.sumOfWeights and not self.isData:
                scaleFactor /= self.sumOfWeights

        if not expression:
            if tree.GetEntryList():
                return tree.GetEntryList().GetN() * scaleFactor
            return self.entries * scaleFactor
        
        weights = getValuesFromTree( tree, expression, selection )[1]
        # FIXME: this is actually not correct, need to treat it as efficiency
        totalYield, uncertainty = DistributionTools.sumOfWeights( weights )
        self.logger.debug( 'getYield(): total yield=%g, total SF=%g, sum of weights=%g, weightExpression= %s' % (totalYield, scaleFactor, self.sumOfWeights, self.weightExpression) )
        return totalYield * scaleFactor, uncertainty * scaleFactor
    
    def getYield( self, cut=Cut(), weightExpression=None, luminosity=1., ignoreWeights=False, systematicVariation=None, ignoreDataWeight=False, ignoreSF=False, systematicsSet=None, recreate=False ):
        hist = self.getHistogram( xVar=var_Yield, title='', cut=cut, weightExpression=weightExpression, drawOption='', style=None, luminosity=luminosity, recreate=recreate, systematicVariation=systematicVariation, includeOverflowBins=False, ignoreDataWeight=ignoreDataWeight, systematicsSet=systematicsSet)
        if ignoreWeights:
            value = hist.GetEntries()
            error = math.sqrt( value )
        else:
            from ROOT import Double
            error = Double(0.0)
            value = hist.IntegralAndError( 0, hist.GetNbinsX()+2 , error )
        return value, error
    
    def getValues( self, xVar, cut=None, weightExpression=None, luminosity=1., systematicVariation=None, systematicsSet=None ):
        ## Gets the values and weights for a given variable and selection
        #  @param xVar                 Variable object defining which values should be calculated
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param systematicsSet       additional systematics that should be considered
        #  @return (values, weights)
        weightExpression = weightExpression if weightExpression else self.weightExpression
        systematicVariation = systematicVariation if systematicVariation else self.nominalSystematics
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        cut = self._determineCut( cut ) * weightExpression * systematicsSet.totalWeight( systematicVariation ).cut
        xVar = self._determineVariable( xVar )
        self.logger.debug( 'getValues(): getting values for %r with cut=%r and sytematics=%r from %r' % (xVar, cut, systematicVariation, self) )
        tree = self._open( systematicVariation.treeName )
        if not tree:
            return
        values, weights = getValuesFromTree( tree, xVar.command, cut.cut )
        sF = self.combinedScaleFactors * systematicsSet.totalScaleFactor( systematicVariation, cut )
        if not self.isData:
            sF *= luminosity
        return values, weights * sF
    
    def getHistogram( self, xVar, title=None, cut=None, weightExpression=None, drawOption='', style=None, luminosity=1., recreate=False,
                      systematicVariation=None, includeOverflowBins=False, ignoreDataWeight=False, systematicsSet=None, forceBinning=False ):
        ## Wrapper for TTree::Draw on the TChain object
        #  If a HistogramStore is defined it will first try to find the histogram in the store. If it does not exist the histogram will be
        #  created as usual and afterwards placed in the HistogramStore. Scale factor, cross section and kFactor are not persisted and always
        #  applied in this command.    The "recreate" option allows to override existing histograms if they are already in the HistogramStore. 
        #  @param xVar                 Variable object that defines the variable expression used in draw and the binning
        #  @param title                defines the histogram title
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param drawOption           ROOT draw option
        #  @param style                Style object (overrides the default style object)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param recreate             force recreation of the histogram (don't read it from a possible histogram file)
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param ignoreDataWeight     used for fake-factor data-mc where weight is to be applied to data via self.weightExpression
        #  @param includeOverflowBins  decide if the entries of the overflow bins should be added to the first and last bins, respectively
        #  @param systematicsSet       additional systematics that should be considered
        #  @return histogram
        self.logger.debug( 'getHistogram(): creating histogram for var=%r with cut=%r and syst=%r from %r' % (xVar, cut, systematicVariation, self) )
        title = title if title else self.title
        style = style if style else self.style
        weightExpression = weightExpression if weightExpression else self.weightExpression
        if ignoreDataWeight and self.isData:
            weightExpression = self.weightExpression
        
        cut = self._determineCut( cut )
        xVar = self._determineVariable( xVar )
        
        systematicVariation = systematicVariation if systematicVariation else self.nominalSystematics
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        systematics = systematicVariation.systematics
        if not systematics or systematics not in systematicsSet:
            systematicVariation = self.nominalSystematics
        
        # try to get the histogram from the store
        hist = None

        storeSystematicVariation = systematicVariation if systematicVariation.isShapeSystematics else self.nominalSystematics
        if self.histogramStore:
            hist = self.histogramStore.getHistogram( self, storeSystematicVariation, xVar, cut )
            if hist:
                self.logger.debug( 'getHistogram(): retrieved Histogram from store %s, yield=%g' % (self.name, hist.Integral()) )
                hist.SetTitle( title )
        
        # create the histogram if necessary
        if not hist or recreate:
            tree = self._open( systematicVariation.treeName )
            if not tree:
                return
            # include the weights from systematics
            weightExpression *= systematicsSet.totalWeight( systematicVariation, cut)
            hist = xVar.createHistogramFromTree( tree, title, cut, weightExpression, drawOption, style )
            #print self.name, hist.Integral()
            if hist and self.sumOfWeights and hist.Integral(0, hist.GetNbinsX()+1) and not self.isData:
                hist.Scale( 1. / self.sumOfWeights )
                self.logger.debug( 'getHistogram(): dividing by sum of weights %g, yield=%g' % (self.sumOfWeights, hist.Integral()) )
            if self.histogramStore:
                self.histogramStore.putHistogram( self, storeSystematicVariation, xVar, cut, hist )
        
        # apply scale factors
        if hist:
            if forceBinning:
                hist = Tools.forceBinning( hist, xVar )
            if includeOverflowBins:
                self.logger.debug( 'getHistogram(): moving overflow entries into first/last bins' )
                hist = overflowIntoLastBins( hist )
            sF = self.combinedScaleFactors * systematicsSet.totalScaleFactor( systematicVariation, cut )
            if not self.isData:
                sF *= luminosity
            hist.Scale( sF )
            self.logger.debug( 'getHistogram(): scaling histogram by %g, total yield=%g' % (sF, hist.Integral()) )
        # apply styling
        if style and hist:
            style.apply( hist )
        # apply blinding
        if self.isData:
            xVar.applyBlinding( cut, hist )
        
        self._close(systematicVariation.treeName)
        return hist
    
    def getHistogram2D( self, xVar, yVar, title=None, cut=None, weightExpression=None, style=None, luminosity=1., recreate=False, systematicVariation=None, profile=False, systematicsSet=None ):
        ## Wrapper for TTree::Draw on the TChain object
        #  If a HistogramStore is defined it will first try to find the histogram in the store. If it does not exist the histogram will be
        #  created as usual and afterwards placed in the HistogramStore. Scale factor, cross section and kFactor are not persisted and always
        #  applied in this command.    The "recreate" option allows to override existing histograms if they are already in the HistogramStore. 
        #  @param xVar                 Variable object that defines the variable expression for x used in draw and the binning
        #  @param yVar                 Variable object that defines the variable expression for y used in draw and the binning
        #  @param title                defines the histogram title
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param style                Style object (overrides the default style object)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param recreate             force recreation of the histogram (don't read it from a possible histogram file)
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param profile              Decide if a TProfile should be created instead of a TH2
        #  @param systematicsSet       additional systematics that should be considered
        #  @return histogram
        
        self.logger.debug( 'getHistogram2D(): creating histogram for xVar=%r and yVar=%r with cut=%r and syst=%r from %r' % (xVar, yVar, cut, systematicVariation, self) )
        
        title = title if title else self.title
        style = style if style else self.style
        weightExpression = weightExpression if weightExpression else self.weightExpression
        systematicVariation = systematicVariation if systematicVariation else self.nominalSystematics
        cut = self._determineCut( cut )
        xVar = self._determineVariable( xVar )
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        
        systematics = systematicVariation.systematics
        if not systematics or systematics not in self.systematicsSet:
            systematicVariation = self.nominalSystematics

        #FIXME: try to get histogram from HistogramStore first
        
        weightExpression *= systematicsSet.totalWeight( systematicVariation, cut)
        tree = self._open( systematicVariation.treeName )
        if not tree:
            return
        hist = create2DHistogramFromTree( tree, xVar, yVar, title, cut, weightExpression, profile )
        if hist and self.sumOfWeights and hist.Integral() and not self.isData:
            hist.Scale( 1. / self.sumOfWeights )
            self.logger.debug( 'getHistogram2D(): dividing by sum of weights %g, yield=%g' % (self.sumOfWeights, hist.Integral()) )
        
        # apply scale factors
        if hist:
            sF = self.combinedScaleFactors * systematicsSet.totalScaleFactor( systematicVariation, cut )
            if not self.isData:
                sF *= luminosity
            hist.Scale( sF )
            self.logger.debug( 'getHistogram2D(): scaling histogram by %g, total yield=%g' % (sF, hist.Integral()) )
        # apply styling
        if style and hist:
            style.apply( hist )
        return hist
        
    def getSystematicsGraph( self, xVar, title=None, cut=None, weightExpression=None, drawOption='', style=None, luminosity=1., recreate=False, includeOverflowBins=False ):
        self.logger.error( 'getSystematicsGraph(): use SystematicsCalculator.calculateSystematicsGraph() instead!' )
    
    def getResolutionGraph( self, xVar, yVar, title=None, measure=None, cut='', weightExpression='', style=None, luminosity=1., recreate=False, systematicVariation=None, systematicsSet=None ):
        ## Create and fill a resolution graph for this dataset, i.e. resolution of y vs. x
        #  @param xVar                 defines the x axis. The binning defines the slicing for different resolution evaluation
        #  @param yVar                 variable for which the resolution is determined 
        #  @param title                title of the graph
        #  @param measure              Measure object to evaluate the resolution (see plotting/ResolutionGraph.py)
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param style                Style object (overrides the default style object)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param recreate             force recreation of the histogram (don't read it from a possible histogram file)
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param systematicsSet       additional systematics that should be considered
        #  @return the filled graph
        self.logger.debug( 'getResolutionGraph(): creating resolution graph for yVar=%r, xVar=%r with cut=%r and syst=%r from %r' % (yVar, xVar, cut, systematicVariation, self) )
        
        bins = xVar.binning.bins
        title = '%s vs %s' % (yVar.title, xVar.title) if title is None else title
        name = 'g%s_%s' % ( title.replace(' ', '_').replace('(', '').replace(')',''), uuid.uuid1() )
        style = style if style else self.style
        weightExpression = weightExpression if weightExpression else self.weightExpression
        systematicVariation = systematicVariation if systematicVariation else self.nominalSystematics
    
        from ROOT import TGraphAsymmErrors
        graph = TGraphAsymmErrors( xVar.binning.nBins )
        graph.SetNameTitle( name, title )
        
        yMean, yErrLow, yErrUp = (0., 0., 0.)
        for iBin in xrange( xVar.binning.nBins ):
            low = bins[iBin]
            up = bins[iBin+1]
            xMean = low + 0.5 * (up - low)
            xErr = xMean - low
            myCut = Cut( '%s >= %s && %s < %s' % (xVar.command, low, xVar.command, up) ) + cut + yVar.defaultCut
            values, weights = self.getValues( yVar, myCut, weightExpression, luminosity, systematicVariation, systematicsSet )
            if measure:
                yMean, yErrLow, yErrUp = measure.calculateFromValues( values, weights )
            else:
                yMean, yErrLow, yErrUp = ( 0., 0., 0. )
            graph.SetPoint( iBin, xMean, yMean )
            graph.SetPointError( iBin, xErr, xErr, yErrLow, yErrUp )
        
        if graph and style:
            style.apply( graph )
        return graph

    def getCutFlowYields( self, cuts, luminosity=None, ignoreWeights=False, systematicVariation=None, accumulateCuts=True, systematicsSet=None, recreate=False ):
        ## Create a dictionary of cuts with their corresponding yields
        #  @param cuts                 list of cuts to apply
        #  @param luminosity           global scale factor
        #  @param ignoreWeights        ignore the weights and any other scale factors including luminosity
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param accumulateCuts       decide if the cuts should be successively combined, ie. second cut applied in addition to first etc.
        #  @param systematicsSet       additional systematics that should be considered
        #  @return dictionary of cut to yield
        result = {}
        myCut = Cut()
        for cut in cuts:
            if accumulateCuts:
                myCut += cut
            else:
                myCut = cut
            result[cut] = self.getYield( cut=myCut, luminosity=luminosity, ignoreWeights=ignoreWeights, systematicVariation=systematicVariation, systematicsSet=systematicsSet, recreate=recreate )
        return result
            
    def getCutflowHistogram( self, cuts, luminosity=None, ignoreWeights=False, cutFlowVariable=None, systematicVariation=None, accumulateCuts=True, systematicsSet=None ):
        ## Create and fill a cutflow histogram for this dataset
        #  @param cuts                 list of cuts to apply
        #  @param luminosity           global scale factor
        #  @param ignoreWeights        ignore the weights and any other scale factors including luminosity
        #  @param cutFlowVariable      use a previously defined variable instead
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param accumulateCuts       decide if the cuts should be successively combined, ie. second cut applied in addition to first etc.
        #  @param systematicsSet       additional systematics that should be considered
        #  @return the filled histogram
        var = cutFlowVariable if cutFlowVariable else createCutFlowVariable( cuts=cuts )
        hist = var.createHistogram( self.title )
        yields = self.getCutFlowYields( cuts, luminosity, ignoreWeights, systematicVariation, accumulateCuts, systematicsSet )
        for index, cut in enumerate( cuts ):
            y, error = yields[cut]
            hist.SetBinContent( index+1, y )
            hist.SetBinError( index+1, error )
        if hist and self.style:
            self.style.apply( hist )
        return hist

    def toString( self ):
        ## String representation used for persistency
        s = '%s; %s; %s; %d; %g; %g; ' % ( self.name, self.title, self.nominalSystematics.treeName, self.style.lineColor, self.crossSection, self.kFactor )
        for fileName in self.fileNames:
            s += '%s, ' % fileName
        s = s.rstrip(', ')
        return s
    
# store all defined processes
PHYSICSPROCESSES={}
    
class PhysicsProcess( Dataset ):
    ## Container class for a set of datasets that should be treated together
    #  Fulfills Dataset interface so it can be nested, i.e. contain other PhysicsProcesses
    logger = logging.getLogger( __name__ + '.PhysicsProcess' )
    
    def __init__( self, name, title='', style=None, kFactor=1.0, isData=False, isSignal=False, isBSMSignal=False, datasets=None,titleLatex='' ):
        ## Default contructor
        #  @param name               name of the physics process used for output file names
        #  @param title              title used for example in legend entries (use TLatex here)
        #  @param style              default Style object associated with this physics process
        #  @param kFactor            correction factor applied as scaling to all histograms
        #  @param isData             this is data (not MC), simply for book keeping
        #  @param isSignal           this is signal MC, simply stored to decide how it is used in MVA training
        #  @param isBSMSignal        this is BSM signal MC, in case that is different from the ordinary signal
        #  @param datasets           list of datasets in this physics process
        self.datasets = []
        Dataset.__init__( self, name, title, None, None, style, None, None, kFactor, isData, isSignal, isBSMSignal, titleLatex )
        self.datasets = datasets if datasets else []
    
    def copy( self, name, title ):
        ## Create a copy of this PhysicsProcess with the given name and title
        #  This does NOT copy underlying datasets, take special care when modifying those
        #  @param name               name of the physics process copy used for output file names
        #  @param title              title used for example in legend entries (use TLatex here)
        #  @return the copied physics process
        process = Dataset.copy( self, name, title )
        #process.systematicsSet = copy(self.systematicsSet)
        return process
        
    @classmethod
    def fromString( cls, s ):
        ## Contructor from string used to read in text files
        #  Format is "name; title; color; kFactor; dataset1, dataset2, ... "
        result = [x.lstrip().rstrip() for x in s.split( ';' )]
        name = result[0] if len(result) > 0 else 'PhysicsProcess'
        title = result[1] if len(result) > 1 else None
        lineColor = int(result[2]) if len(result) > 2 else 0
        kFactor = float(result[3]) if len(result) > 3 else 1.
        datasetsString = result[4] if len(result) > 4 else ''
        datasetStrings = datasetsString.split( ',' )
        datasets = []
        for datasetString in datasetStrings:
            datasetName = datasetString.strip()
            if not datasetName:
                continue
            if DATASETS.has_key( datasetName ):
                datasets.append( DATASETS[ datasetName ] )
            else:
                cls.logger.error( 'fromString(): Unknown dataset "%s"' % datasetName )
        return cls( name, title, Style(lineColor), kFactor, datasets )
    
    @classmethod
    def fromXML( cls, element ):
        ## Constructor from an XML element
        #  <PhysicsProcess name="" title="" isData="" isSignal="" kFactor="">
        #    <Style color="5"/>
        #    <Dataset name=""/>
        #    <PhysicsProcess name=""/>
        #    <AddCuts>
        #      <Cut> Cut1 </Cut>
        #      <Cut> Cut2 </Cut>
        #    </AddCuts>
        #    <IgnoreCuts>
        #      <Cut> Cut3 </Cut>
        #      <Cut> Cut4 </Cut>
        #    </IgnoreCuts>
        #  </PhysicsProcess>
        #  @param element    the XML element
        #  @return the HistogramStore object
        attributes = element.attrib
        name = attributes[ 'name' ]
        if PHYSICSPROCESSES.has_key( name ):
            return PHYSICSPROCESSES[name]
        process = cls( name )
        if attributes.has_key( 'title' ):
            process.title = attributes['title']
        if attributes.has_key( 'isData' ):
            process.isData = string2bool(attributes['isData'])
        if attributes.has_key( 'isSignal' ):
            process.isSignal = string2bool(attributes['isSignal'])
        if attributes.has_key( 'isBSMSignal' ):
            process.isBSMSignal = string2bool(attributes['isBSMSignal'])
        if attributes.has_key( 'kFactor' ):
            process.kFactor = float(attributes['kFactor'])
        process.style = Style.fromXML( element.find( 'Style' ) ) if element.find( 'Style' ) is not None else None
        for datasetElement in element.findall( 'Dataset' ):
            process.datasets.append( Dataset.fromXML(datasetElement) )
        for processElement in element.findall( 'PhysicsProcess' ):
            process.datasets.append( Dataset.fromXML(processElement) )
        if element.find( 'AddCuts' ) is not None:
            for cutElement in element.find( 'AddCuts' ).findall( 'Cut' ):
                process.addCuts.append( Cut.fromXML( cutElement ) )
        if element.find( 'IgnoreCuts' ) is not None:
            for cutElement in element.find( 'IgnoreCuts' ).findall( 'Cut' ):
                process.ignoreCuts.append( Cut.fromXML( cutElement ) )
        return process
    
    def __repr__( self ):
        return 'PhysicsProcess(%s)' % self.name
    
    def __str__( self ):
        return '%s, %s' % (Dataset.__str__(self), self.datasets)
    
    @property
    def md5( self ):
        md5 = hashlib.md5()
        for dataset in self.datasets:
            md5.update( dataset.md5 )
        return md5.hashdigest()
    
    def _open( self, treeName=None ):
        # nothing to do
        pass
    
    def _register( self, oldName='' ):
        if oldName and PHYSICSPROCESSES.has_key( oldName ) and PHYSICSPROCESSES[ oldName ] == self:
            del PHYSICSPROCESSES[ oldName ]
        if PHYSICSPROCESSES.has_key( self.name ) and PHYSICSPROCESSES[ self.name ] is not self:
            self.logger.warning( 'name(): registering a PhysicsProcess with an already existing name: "%s"' % self.name )
        PHYSICSPROCESSES[ self.name ] = self
    
    @property 
    def crossSection( self ):
        ## Get the combined cross section in pb of all included datasets
        crossSection = 0.
        for dataset in self.datasets:
            crossSection += dataset.crossSection
        return crossSection
    
    @crossSection.setter 
    def crossSection( self, value ):
        if value is not None:
            self.logger.warning( 'crossSection: can not set cross section of %r' % self )
        pass
    
    @property
    def effectiveCrossSection(self):
        ## Get the effective cross section in pb including correction factors
        crossSection = 0.
        for dataset in self.datasets:
            crossSection += dataset.effectiveCrossSection
        return crossSection * self.combinedScaleFactors * self.systematicsSet.totalScaleFactor()
    
    
    @property
    def entries( self ):
        ## Get the combined entries of all included datasets
        entries = 0
        for dataset in self.datasets:
            entries += dataset.entries
        return entries
    
    @property
    def trueDatasets( self ):
        ## Get list of all contained datasets
        #  Recursively resolves all PhysicsProcess daughters, only returns Dataset objects
        datasets = []
        for dataset in self.datasets:
            datasets.extend( dataset.trueDatasets )
        return datasets
    
    @property
    def combinedSystematicsSet( self ):
        ## Get the combined SystematicsSet of this PhysicsProcess and all daughters
        result = set( self.systematicsSet )
        for dataset in self.datasets:
            result |= dataset.combinedSystematicsSet
        return result
    
    @property
    def preselection( self ):
        ## Get the current preselection that is applied
        for dataset in self.datasets:
            return dataset.preselection
    
    @preselection.setter
    def preselection( self, cut=Cut() ):
        ## Define a preselection for this dataset using the TEntryList functionality of TTree
        #  Use an empty cut to reset the preselection
        #  WARNING: this selection is always active even if a looser selection is drawn
        #  @param cut    the preselection cut to apply
        for dataset in self.datasets:
            dataset.preselection = cut
            
    @property
    def weightExpression( self ):
        ## Get the weight expressions for all contained Datasets
        #result = {}
        #for dataset in self.trueDatasets:
        #    result[dataset] = dataset.weightExpression
        return None
    
    @weightExpression.setter
    def weightExpression( self, expression ):
        ## Set the weight expression applied to all contained Datasets
        self.__weightExpression = expression
        for dataset in self.datasets:
            dataset.weightExpression = expression
            
    @property
    def sumOfWeights( self ):
        ## Get the total sum of weights of this dataset. Used to take into account cut efficiencies correctly
        result = 0
        for dataset in self.datasets:
            result += dataset.sumOfWeights
        return result
    
    @sumOfWeights.setter
    def sumOfWeights( self, expression ):
        ## Not useful
        pass
            
    @property
    def dsid( self ):
        ## Get the list of DSIDs contained
        result = []
        for dataset in self.trueDatasets:
            result.append( dataset.dsid )
        return result
    
    @dsid.setter
    def dsid( self, dsid ):
        ## Set the DSID and update cross section, k-factor and filter efficiency from DB
        raise NotImplementedError( 'PhysicsProcess can not set DSID' )
    
    def addSystematicsToAllDaughters( self, systematics ):
        ## Add a Systematics object to all daughter Datasets
        for dataset in self.datasets:
            dataset.addSystematicsToAllDaughters( systematics )
        
    def removeSystematicsFromAllDaughters( self, systematics ):
        ## Remove a Systematics object from this Dataset
        self.systematicsSet.discard( systematics )
        for dataset in self.datasets:
            dataset.removeSystematicsFromAllDaughters( systematics )
    
    def addFriendTreeToAllDaughters( self, friendTree ):
        ## Add a FriendTree object to all contained datasets
        for dataset in self.datasets:
            dataset.addFriendTreeToAllDaughters( friendTree )
    
    def save( self, directory='./', selection=None ):
        ## Stores all contained datasets in the given directory using the given preselection
        #  @param directory     name of the output directory. File names are "<dataset.name>.root"
        #  @param selection     event selection applied to the trees (default preselection if defined)
        for dataset in self.datasets:
            dataset.save( os.path.join( directory, dataset.name + '.root' ) )
    
    def addToTmvaFactory( self, factory, cut=Cut(), weightExpression=None, luminosity=1., className='Background', tmvaWeightBranch='TmvaWeight', systematicsSet=None, scaleFactor=1. ):
        ## Add the tree of this dataset to a TMVA factory
        #  An in memory copy of the tree is created with the selection cut already applied.
        #  Then a branch with the combined event weight is computed and the tree is added to the TMVA::Factory object
        #  @param factory              TMVA::Factory object to which this tree will be added
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data 
        #  @param className            class used in the classification (usually "Signal" or "Background")
        #  @param systematicsSet       additional systematics that should be considered
        #  @param scaleFactpr          additional scale factor that should be considered
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        scaleFactor *= self.combinedScaleFactors
        cut = self._determineCut( cut )
        for dataset in self.datasets:
            dataset.addToTmvaFactory( factory, cut, weightExpression, luminosity, className, tmvaWeightBranch, systematicsSet, scaleFactor )
    
    def getYieldSlow( self, cut=Cut(), weightExpression=None, luminosity=1., ignoreWeights=False, systematicVariation=None, ignoreDataWeight=False, ignoreSF=False ,systematicsSet=None ):
        ## Calculate the expected yield for the given selection
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param ignoreWeights        ignore the weights and any other scale factors including luminosity
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param ignoreDataWeight     used for fake-factor data-mc where weight is to be applied to data via self.weightExpression
        #  @param systematicsSet       additional systematics that should be considered
        #  @return the yield
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        totalYield = 0
        totalUncertainty = 0
        cut = self._determineCut( cut )
        for dataset in self.datasets:
            y, error = dataset.getYield( cut, weightExpression, luminosity, ignoreWeights, systematicVariation, ignoreDataWeight, ignoreSF, systematicsSet )
            totalYield += y
            totalUncertainty = math.sqrt( totalUncertainty**2 + error**2 )
        return totalYield * self.combinedScaleFactors, totalUncertainty * self.combinedScaleFactors
    
    def getValues( self, xVar, cut=None, weightExpression=None, luminosity=1., systematicVariation=None, systematicsSet=None ):
        ## Gets the values and weights for a given variable and selection
        #  @param xVar                 Variable object defining which values should be calculated
        #  @param cut                  Cut object that defines the applied cut
        #  @param weightExpression     weight expression (overrides the default weight expression)
        #  @param luminosity           global scale factor, i.e. integrated luminosity, not applied for data
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param ignoreDataWeight     used for fake-factor data-mc where weight is to be applied to data via self.weightExpression
        #  @param systematicsSet       additional systematics that should be considered
        #  @return (values, weights)
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        import numpy
        values = numpy.empty( [0.] )
        weights = numpy.empty( [0.] )
        cut = self._determineCut( cut )
        xVar = self._determineVariable( xVar )
        for dataset in self.datasets:
            v, w = dataset.getValues( xVar, cut, weightExpression, luminosity, systematicVariation, systematicsSet )
            values = numpy.append( values, v )
            weights = numpy.append( weights, w )
        return values, weights * self.combinedScaleFactors
        
    def getHistogram( self, xVar, title=None, cut=None, weightExpression=None, drawOption='', style=None, luminosity=1., recreate=False, systematicVariation=None,
                      includeOverflowBins=False, ignoreDataWeight=False, systematicsSet=None, forceBinning=False ):
        ## Get the combined histogram of all contained datasets
        #  @param xVar                 Variable object that defines the variable expresseion used in draw and the binning
        #  @param title                defines the histogram title
        #  @param cut                  TCut object that defines the applied cut
        #  @param weight               weight expression (overrides the default weight expression)
        #  @param drawOption           ROOT draw option
        #  @param style                Style object (overrides the default style object)
        #  @param luminosity           global scale factor, i.e. integrated luminosity
        #  @param recreate             force recreation of the histogram (don't read it from a possible histogram file)
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param ignoreDataWeight     used for fake-factor data-mc where weight is to be applied to data via self.weightExpression
        #  @param systematicsSet       additional systematics that should be considered
        #  @return histogram
        self.logger.debug( 'getHistogram(): creating histogram for var=%r with cut=%r and syst=%r from %r' % (xVar, cut, systematicVariation, self) )
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        title = title if title else self.title
        style = style if style else self.style
        cut = self._determineCut( cut )
        xVar = self._determineVariable( xVar )
        histogram = None
        for dataset in self.datasets:
            h = dataset.getHistogram( xVar, title, cut, weightExpression, drawOption, style, luminosity, recreate, systematicVariation, includeOverflowBins, ignoreDataWeight, systematicsSet, forceBinning )
            if not h:
                self.logger.warning( 'getHistogram(): no histogram created for: dataset=%r, var=%r, cut=%r' % ( dataset, xVar, cut ) )
                continue
            if not histogram:
                histogram = h
                histogram.SetTitle( title )
            else:
                histogram.Add( h )
        if histogram:
            if style:
                style.apply( histogram )
            histogram.Scale( self.combinedScaleFactors )
            self.logger.debug( 'getHistogram(): scaling histogram by %g, total yield=%g' % (self.combinedScaleFactors, histogram.Integral()) )
        return histogram
    
    def getHistogram2D( self, xVar, yVar, title=None, cut=None, weight=None, style=None, luminosity=1., recreate=False, systematicVariation=None, profile=False, systematicsSet=None ):
        ## Get the combined histogram of all contained datasets
        #  @param xVar                 Variable object that defines the variable expression for x used in draw and the binning
        #  @param yVar                 Variable object that defines the variable expression for y used in draw and the binning
        #  @param title                defines the histogram title
        #  @param cut                  TCut object that defines the applied cut
        #  @param weight               weight expression (overrides the default weight expression)
        #  @param style                Style object (overrides the default style object)
        #  @param luminosity           global scale factor, i.e. integrated luminosity
        #  @param recreate             force recreation of the histogram (don't read it from a possible histogram file)
        #  @param systematicVariation  SytematicVariation object defining the tree name and potential additional weights
        #  @param profile              Decide if a TProfile should be created instead of a TH2
        #  @param systematicsSet       additional systematics that should be considered
        #  @return histogram
        self.logger.debug( 'getHistogram2D(): creating histogram for var=%r with cut=%r and syst=%r from %r' % (xVar, cut, systematicVariation, self) )
        systematicsSet = self.systematicsSet.union( systematicsSet ) if systematicsSet else self.systematicsSet
        title = title if title else self.title
        style = style if style else self.style
        cut = self._determineCut( cut )
        xVar = self._determineVariable( xVar )
        yVar = self._determineVariable( yVar )
        histogram = None
        for dataset in self.datasets:
            h = dataset.getHistogram2D( xVar, yVar, title, cut, weight, style, luminosity, recreate, systematicVariation, profile )
            if not h:
                self.logger.warning( 'getHistogram2D(): no histogram created for: dataset=%r, xVar=%r, yVar=%r, cut=%r' % ( dataset, xVar, yVar, cut ) )
                continue
            if not histogram:
                histogram = h
                histogram.SetTitle( title )
            else:
                histogram.Add( h )
        if histogram:
            if style:
                style.apply( histogram )
            histogram.Scale( self.combinedScaleFactors )
        return histogram
    
    def toString( self ):
        ## String representation used for persistency
        s = '%s; %s; %d; %g; ' % (self.name, self.title, self.style.lineColor, self.kFactor)
        for dataset in self.datasets:
            s += '%s, ' % dataset.name
        s = s.rstrip(', ')
        return s
    
def createCutflowPlot( datasets, cuts, luminosity=None, ignoreWeights=False ):
    ## Helper method to create a cutflow histogram. Stacked if a luminosity scaling is requested
    #  @param datasets       list of datasets to include
    #  @param cuts           list of cuts that define the cutflow
    #  @param luminosity     luminosity to scale to
    #  @param ignoreWeights  set if all weights should be ignored
    #  @return the plot object
    var = createCutFlowVariable( cuts=cuts )
    p = BasicPlot( 'Cutflow', var )
    p.showBinWidthY = False
    if luminosity:
        p.titles.append( '#scale[0.5]{#int}Ldt = %.2g fb^{-1}' % (luminosity / 1000.) )
    for dataset in datasets:
        p.addHistogram( dataset.getCutflowHistogram(cuts, luminosity, ignoreWeights), stacked=luminosity is not None )
    return p

def writeDatasetsToTextFile( fileName, datasets ):
    ## Helper method to persist dataset definition in a text file
    #  @param fileName    name of the output text file
    #  @param datasets    list of datasets to persist
    f = open( fileName, 'w' )
    f.write( '# Datasets\n' )
    f.write( '# name; title; treeName; color; crossSection; kFactor; fileName1, fileName2, ...\n' )
    for dataset in datasets:
        f.write( dataset.toString() + '\n' )
    f.close()
    
def readDatasetsFromTextFile( fileName, cls=Dataset ):
    ## Helper method to read dataset definitions from a text file
    #  @param fileName    name of the input text file
    #  @param cls         class to use to interpret the text file
    #  @return list of datasets
    f = open( fileName )
    datasets = []
    for line in f:
        line = line.lstrip().rstrip()
        if not line or line[0] in ['#', '/']:
            continue
        datasets.append( cls.fromString( line ) )
    f.close()
    return datasets

def writePhysicsProcessesToTextFile( fileName, physicsProcesses ):
    ## Helper method to persist physics processes definition in a text file
    #  @param fileName    name of the output text file
    #  @param datasets    list of physics processes to persist
    f = open( fileName, 'w' )
    f.write( '# PhysicsProcesses\n' )
    f.write( '# name; title; color; kFactor; dataset1, dataset2, ...\n' )
    for physicsProcess in physicsProcesses:
        f.write( physicsProcess.toString() + '\n' )
    f.close()

def readPhysicsProcessesFromFile( fileName, cls=PhysicsProcess ):
    ## Helper method to read physics process definitions from a text file
    #  @param fileName    name of the input text file
    #  @param cls         class to use to interpret the text file
    #  @return list of physics processes
    f = open( fileName )
    physicsProcesses = []
    for line in f:
        line = line.lstrip().rstrip()
        if not line or line[0] in ['#', '/']:
            continue
        physicsProcesses.append( cls.fromString( line ) )
    f.close()
    return physicsProcesses        


if __name__ == '__main__':
    from ROOT import TTree, TFile, TRandom3, TCut, kBlue, kRed
    from array import array
    from AtlasStyle import redLine, blueLine, greenLine, orangeLine, blackLine
    from Variable import Binning, Variable
    from Systematics import Systematics
    
    #logging.root.setLevel( logging.DEBUG )
     
    # create some dummy trees in a dummy file
    f = TFile( 'temp.root', 'recreate' )
    rndm = TRandom3()
    mass1 = array( 'f', [0] )
    mass2 = array( 'f', [0] )
    mass3 = array( 'f', [0] )
    mass4 = array( 'f', [0] )
    weight = array( 'f', [0] )
    t1 = TTree( 'tree1', 'tree1' )
    t2 = TTree( 'tree2', 'tree2' )
    t3 = TTree( 'tree3', 'tree3' )
    t4 = TTree( 'data', 'data' )
    t1.Branch( 'mass', mass1, 'mass/F' )
    t1.Branch( 'weight', weight, 'weight/F' )
    t2.Branch( 'mass', mass2, 'mass/F' )
    t2.Branch( 'weight', weight, 'weight/F' )
    t3.Branch( 'mass', mass3, 'mass/F' )
    t3.Branch( 'weight', weight, 'weight/F' )
    t4.Branch( 'mass', mass4, 'mass/F' )
    t4.Branch( 'weight', weight, 'weight/F' )
    sumOfWeights = 0.
    for entry in xrange( 10000 ):
        mass1[0] = rndm.Gaus( 5, 8 )
        mass2[0] = rndm.Gaus( 20, 5 )
        mass3[0] = rndm.Gaus( 15, 1 )
        weight[0] = rndm.Gaus( 1.2, 0.1 )
        sumOfWeights += weight[0]
        t1.Fill()
        t2.Fill()
        t3.Fill()
    weight[0] = 1.0
    for entry in xrange( int(1.2*5*2500) ):
        mass4[0] = rndm.Gaus( 5, 8 )
        t4.Fill()
    for entry in xrange( int(1.2*2500) ):
        mass4[0] = rndm.Gaus( 20, 5 )
        t4.Fill()
    f.Write()
    f.Close()
 
    # create the datasets
    dataset1 = Dataset( 'background1', 'Background 1', ['temp.root'], 'tree1', orangeLine, 'weight', crossSection=5.0 )
    dataset2 = Dataset( 'background2', 'Background 2', ['temp.root'], 'tree2', greenLine, 'weight', crossSection=1.0 )
    dataset3 = Dataset( 'signal', 'Signal', ['temp.root'], 'tree3', redLine, 'weight', crossSection=0.1 )
    data = Dataset( 'data', 'Data', ['temp.root'], 'data', blackLine, 'weight', isData=True )
     
    # alternatively we can also build the physics process from a string representation
    # the format is "name; title; treeName; color; crossSection; kFactor; fileName1, fileName2, ... "
    dataset21 = Dataset.fromString( 'background21; Background 1; tree1; 801; 5.0; 1.0; temp.root' )
    dataset22 = Dataset.fromString( 'background22; Background 2; tree2; 417; 1.0; 1.0; temp.root' )
    dataset23 = Dataset.fromString( 'signal2; Signal; tree3; 632; 0.1; 1.0; temp.root' )
     
    # we can define a HistogramStore which will store all created histograms for faster recreation of plots with the same histograms
    Dataset.histogramStore = HistogramStore( 'hist.root' )
     
    # combine some datasets into a physics process
    backgrounds = PhysicsProcess( 'backgrounds', 'Background x 1.2', blueLine, datasets=[dataset1, dataset2] )
    # add another scale factor
    backgrounds.scaleFactors['rQCD'] = 1.2
    print backgrounds
     
    # alternatively we can also build the physics process from a string representation. The dataset objects with the referenced names have to exist before.
    # the format is "name; title; color; kFactor; dataset1, dataset2, ... "
    backgrounds2 = PhysicsProcess.fromString( 'backgrounds2; Background 2; 600; 1.0; background21, background22' )
    
    # manually set the sum of weights
    for dataset in [ dataset1, dataset2, dataset3, dataset21, dataset22, dataset23 ]:
        dataset.sumOfWeights = sumOfWeights
    data.sumOfWeights = 12000
    
    # define the global lumi scaling in pb-1
    luminosity = 2500.
    
    # get the yield for a certain cut
    print 'Yield from "backgrounds" with "mass>5:"', backgrounds.getYield( Cut('mass > 5'), luminosity=luminosity )
    print 'Yield from "backgrounds" with "mass>15:"', backgrounds.getYield( Cut('mass > 15'), luminosity=luminosity )
     
    # create and draw a cut flow diagram for all datasets
    cutFlowPlot = createCutflowPlot( [dataset1, dataset2, dataset3], [Cut('', 'All Events' ), Cut('mass > 5', 'M > 5 GeV'), Cut('mass > 15', 'M > 15 GeV')], luminosity )
    cutFlowPlot.draw()
     
    # define a variable object for each branch
    massVar = Variable( 'mass', title='M', unit='GeV', binning=Binning(50, 0., 25.) )
    # define some blinded bins, applied to data only
    massVar.blindRegion( Cut(''), 12., 18.)
     
    # define a generic preselection to improve performance
    backgrounds.preselection = Cut( 'mass > 2' )
    data.preselection = Cut( 'mass > 2' )
    
    # create a plot using the BasicPlot class
    testPlot = BasicPlot( 'Dataset Test', massVar )
    # add background and signal datasets as stacked
    testPlot.addHistogram( backgrounds.getHistogram( massVar, luminosity=luminosity ), stacked=True )
    testPlot.addHistogram( dataset3.getHistogram( massVar, luminosity=luminosity ), stacked=True )
    # add lines to indicate the two background components (note that they are automatically weighed by their cross section)
    testPlot.addHistogram( dataset1.getHistogram( massVar, luminosity=luminosity ), drawOption='HIST' )
    testPlot.addHistogram( dataset2.getHistogram( massVar, luminosity=luminosity ), drawOption='HIST' )
    testPlot.addHistogram( data.getHistogram( massVar ), drawOption='E0' )
    # draw the plot
    testPlot.draw()
    
    # example for including systematics
    systematicsPlot = BasicPlot( 'Systematics Test', massVar )
    # define a simple scale uncertainty +10%, -5%
    backgrounds.systematicsSet.add( Systematics.scaleSystematics( 'scaleSyst', upScale=1.1, downScale=0.95 ) )
    # add the nominal histogram with statistical uncertainties only
    h = backgrounds.getHistogram( massVar )
    s = backgrounds.getSystematicsGraph( massVar, 'Systematics', style=Style( kRed, fillStyle=3004 ) )
    systematicsPlot.addSystematicsGraph( h, s )
    systematicsPlot.addHistogram( h, 'E0' )
    systematicsPlot.combineStatsAndSyst = False
    systematicsPlot.draw()
    
    
    # example for a 2D histogram
    weightVar = Variable('weight', title='Weight', binning=VariableBinning([0.8,0.9,1.2,1.7,1.8]))
    plot2D = BasicPlot( '2D Test', massVar, weightVar )
    plot2D.addHistogram( backgrounds.getHistogram2D( massVar, weightVar, luminosity=1000 ), 'COLZ' )
    plot2D.draw()
    
     
    # Datasets and PhysicsProcesses can be persisted in text files
    writeDatasetsToTextFile( 'datasets.txt', DATASETS.values() )
    writePhysicsProcessesToTextFile( 'processes.txt', PHYSICSPROCESSES.values() )
    print 'Reading datasets from "datasets.txt":', readDatasetsFromTextFile( 'datasets.txt' )
    print 'Reading processes from "processes.txt":', readPhysicsProcessesFromFile( 'processes.txt' )
 
    raw_input( 'Continue?' )
     
    # clean up and delete temp file
    os.remove( 'temp.root' )
    os.remove( 'datasets.txt' )
    os.remove( 'processes.txt' )
    
