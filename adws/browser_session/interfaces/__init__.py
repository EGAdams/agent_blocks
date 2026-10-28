"""Every interface browser_session's pieces are written against -- no
concretes anywhere under this package. Split one module per concern
(Interface Segregation), and re-exported here so a caller can say
`from browser_session.interfaces import IBrowserSession`.
"""
from .attaching import ( IBrowserAttacher, IBrowserManager, IBrowserManagerFactory, IDetachPolicy,
                         IDriverFactory, IDriverService, ISwitchTo, IWindowDriver )
from .catalog import ITargetCatalog
from .clicking import IClickAction, IClickPartsFactory
from .launching import IChromeCommandBuilder, IChromeLauncher, IProcessSpawner, IProfileDirResolver
from .locating import IElementLocator, ILocatorBuilder, ILocatorChain, ILocatorFactory
from .matching import IElementSpecification, ITextMatcher, IUrlSpecification
from .page import IBrowserSession, IElementFinder, IPageElement, IReleasable, ITabNavigator
from .probing import IEndpointProbe, IJsonFetcher, IReadinessWaiter
from .reporting import IClickReporter, IDebugOpenReporter
from .selecting import ITabSelector
from .system import IClock

__all__ = [
    "IBrowserAttacher", "IBrowserManager", "IBrowserManagerFactory", "IBrowserSession",
    "IChromeCommandBuilder", "IChromeLauncher", "IClickAction", "IClickPartsFactory",
    "IClickReporter", "IClock", "IDebugOpenReporter", "IDetachPolicy", "IDriverFactory",
    "IDriverService", "IElementFinder", "IElementLocator", "IElementSpecification",
    "IEndpointProbe", "IJsonFetcher", "ILocatorBuilder", "ILocatorChain", "ILocatorFactory",
    "IPageElement", "IProcessSpawner", "IProfileDirResolver", "IReadinessWaiter", "IReleasable",
    "ISwitchTo", "ITabNavigator", "ITabSelector", "ITargetCatalog", "ITextMatcher",
    "IUrlSpecification", "IWindowDriver",
]
