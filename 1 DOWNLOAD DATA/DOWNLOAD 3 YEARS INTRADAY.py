#!/usr/bin/env python3
"""
DOWNLOAD INTRADAY DATA.py

Downloads 15-minute candles for the 400 most liquid NSE stocks, one year back,
and saves them to  DATA\\intraday_bars.json

WHAT IT IS FOR
    Every backtest so far has used DAILY bars. That is enough to judge a swing
    setup, and it is useless for judging an intraday one: PULLBACK, SQUEEZE,
    VWAP-FLIP and ORB are 96% of the desk's trades and none of them has ever
    been tested, because VWAP and an opening range cannot be reconstructed
    from a daily candle.

    It is also the only way to settle a question from 10/09. A candlestick
    pattern at a level tested well when the entry happened INSIDE the signal
    bar and badly when it waited for the next one - a Rs 4,860 per trade
    difference that is purely the gap between a close and the next open. On a
    daily chart that gap is a whole night. On a 15-minute chart it is fifteen
    minutes. That is a different question and it needs this data.

HOW TO RUN IT
    1. Run  connect.py  first, so today's Zerodha login is fresh.
    2. Double-click this file.
    3. Leave it alone for about ten minutes. It prints its progress.

    If it stops early - laptop asleep, connection dropped - just run it again.
    It keeps what it already has and carries on from there.

WHAT IT COSTS
    Nothing. It uses the historical-data access the desk already uses. It makes
    about 1,200 requests, paced under Zerodha's limit.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "DATA")
FEED = os.path.join(ROOT, "5 LIVE FEED")
OUT = os.path.join(DATA, "intraday_bars_3y.json")

MONTHS_BACK = 36
INTERVAL = "15minute"
CHUNK = 100          # days per request - Kite's limit for 15minute is 200
PACE = 0.36          # seconds between requests


def hold(code=0):
    try:
        input("\n  Press Enter to close. ")
    except Exception:
        pass
    sys.exit(code)


SYMBOLS = ["20MICRONS", "360ONE", "3MINDIA", "5PAISA", "63MOONS", "AADHARHFC", "AARTIDRUGS", "AARTIIND", "AARTIPHARM", "AAVAS", "ABB", "ABBOTINDIA", "ABCAPITAL", "ABDL", "ABFRL", "ABLBL", "ABREL", "ABSLAMC", "ACC", "ACE", "ACI", "ACMESOLAR", "ACUTAAS", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS", "ADANIPOWER", "ADFFOODS", "ADOR", "ADSL", "ADVENZYMES", "AEGISLOG", "AEGISVOPAK", "AEROENTER", "AEROFLEX", "AETHER", "AFCONS", "AFFLE", "AGARIND", "AGARWALEYE", "AGI", "AGIIL", "AHLUCONT", "AIAENG", "AIIL", "AJANTPHARM", "AJAXENGG", "AJMERA", "AKUMS", "ALEMBICLTD", "ALIVUS", "ALKEM", "ALKYLAMINE", "ALLCARGO", "ALLTIME", "ALOKINDS", "AMBER", "AMBIKCO", "AMBUJACEM", "AMRUTANJAN", "ANANDRATHI", "ANANTRAJ", "ANDHRSUGAR", "ANGELONE", "ANTELOPUS", "ANTHEM", "ANUP", "ANURAS", "APARINDS", "APCOTEXIND", "APEX", "APLAPOLLO", "APLLTD", "APOLLO", "APOLLOHOSP", "APOLLOPIPE", "APOLLOTYRE", "APTUS", "AQYLON", "ARE&M", "ARFIN", "ARIHANTCAP", "ARIS", "ARKADE", "ARMANFIN", "ARTEMISMED", "ARVIND", "ARVINDFASN", "ARVSMART", "ASAHIINDIA", "ASALCBR", "ASHAPURMIN", "ASHIANA", "ASHOKA", "ASHOKLEY", "ASIANENE", "ASIANPAINT", "ASIANTILES", "ASKAUTOLTD", "ASTEC", "ASTERDM", "ASTRAL", "ASTRAMICRO", "ASTRAZEN", "ATALREAL", "ATGL", "ATHERENERG", "ATUL", "ATULAUTO", "AUBANK", "AURIONPRO", "AUROPHARMA", "AURUM", "AUTOAXLES", "AVADHSUGAR", "AVALON", "AVANTEL", "AVANTIFEED", "AVL", "AWFIS", "AWHCL", "AWL", "AXISBANK", "AXISCADES", "AZAD", "BAJAJCON", "BAJAJELEC", "BAJAJFINSV", "BAJAJHCARE", "BAJAJHFL", "BAJAJHIND", "BAJAJHLDNG", "BAJEL", "BAJFINANCE", "BALAJITELE", "BALAMINES", "BALKRISIND", "BALMLAWRIE", "BALRAMCHIN", "BALUFORGE", "BANCOINDIA", "BANDHANBNK", "BANKBARODA", "BANKINDIA", "BANSALWIRE", "BASF", "BATAINDIA", "BAYERCROP", "BBL", "BBOX", "BBTC", "BCG", "BCLIND", "BDL", "BECTORFOOD", "BEL", "BELRISE", "BEML", "BEPL", "BERGEPAINT", "BETA", "BFINVEST", "BFUTILITIE", "BHAGCHEM", "BHAGYANGR", "BHARATFORG", "BHARATRAS", "BHARATSE", "BHARATWIRE", "BHARTIARTL", "BHARTIHEXA", "BHEL", "BIKAJI", "BIOCON", "BIRLACORPN", "BIRLANU", "BLACKBUCK", "BLKASHYAP", "BLS", "BLSE", "BLUEDART", "BLUEJET", "BLUESTARCO", "BLUESTONE", "BLUSPRING", "BODALCHEM", "BOMDYEING", "BORANA", "BOROLTD", "BORORENEW", "BOROSCI", "BOSCHLTD", "BPCL", "BRIGADE", "BRITANNIA", "BSE", "BSOFT", "BUTTERFLY", "CAMLINFINE", "CAMPUS", "CAMS", "CANBK", "CANFINHOME", "CANTABIL", "CAPACITE", "CAPLIPOINT", "CARBORUNIV", "CARERATING", "CARRARO", "CARTRADE", "CARYSIL", "CASTROLIND", "CCAVENUE", "CCL", "CDSL", "CEATLTD", "CEIGALL", "CELLO", "CEMPRO", "CENTENKA", "CENTRALBK", "CENTUM", "CENTURYPLY", "CERA", "CESC", "CEWATER", "CGCL", "CGPOWER", "CHALET", "CHAMBLFERT", "CHEMPLASTS", "CHENNPETRO", "CHOICEIN", "CHOLAFIN", "CHOLAHLDNG", "CIEINDIA", "CIPLA", "CLEAN", "CLSEL", "CMSINFO", "CNL", "COALINDIA", "COCHINSHIP", "COFORGE", "COHANCE", "COLPAL", "CONCOR", "CONCORDBIO", "CONFIPET", "CONTROLPR", "COROMANDEL", "COSMOFIRST", "CPPLUS", "CRAFTSMAN", "CREDITACC", "CRISIL", "CRIZAC", "CROMPTON", "CSBBANK", "CUB", "CUMMINSIND", "CUPID", "CYIENT", "CYIENTDLM", "DABUR", "DALBHARAT", "DALMIASUG", "DAMCAPITAL", "DATAMATICS", "DATAPATTNS", "DAVANGERE", "DBCORP", "DBL", "DBREALTY", "DCAL", "DCBBANK", "DCMSHRIRAM", "DCW", "DCXINDIA", "DDEVPLSTIK", "DECCANCE", "DEEDEV", "DEEPAKFERT", "DEEPAKNTR", "DEEPINDS", "DELHIVERY", "DELTACORP", "DEN", "DENTA", "DEVYANI", "DHAMPURSUG", "DHANBANK", "DHANUKA", "DHARMAJ", "DIAMONDYD", "DIFFNKG", "DIGITIDE", "DIVGIITTS", "DIVISLAB", "DIXON", "DJML", "DLF", "DLINKINDIA", "DMART", "DMCC", "DODLA", "DOLLAR", "DOLPHIN", "DOMS", "DPABHUSHAN", "DREDGECORP", "DRREDDY", "DSSL", "DWARKESH", "DYCL", "DYNAMATECH", "EASEMYTRIP", "EBGNG", "ECLERX", "ECOSMOBLTY", "EDELWEISS", "EFCIL", "EICHERMOT", "EIDPARRY", "EIEL", "EIHOTEL", "EIMCOELECO", "EKC", "ELECON", "ELECTCAST", "ELECTHERM", "ELGIEQUIP", "ELIN", "ELLEN", "EMAMILTD", "EMBDL", "EMCURE", "EMIL", "EMSLIMITED", "EMUDHRA", "ENDURANCE", "ENGINERSIN", "ENRIN", "ENTERO", "EPACK", "EPIGRAL", "EPL", "EQUITASBNK", "ERIS", "ESABINDIA", "ESCORTS", "ETERNAL", "ETHOSLTD", "EUREKAFORB", "EVEREADY", "EXICOM", "EXIDEIND", "EXPLEOSOL", "FACT", "FCL", "FDC", "FEDERALBNK", "FEDFINA", "FIEMIND", "FILATEX", "FINCABLES", "FINEORG", "FINOPB", "FINPIPE", "FIRSTCRY", "FISCHER", "FIVESTAR", "FLAIR", "FLUOROCHEM", "FMGOETZE", "FORCEMOT", "FORTIS", "FOSECOIND", "FSL", "FUSION", "GABRIEL", "GAEL", "GAIL", "GALAPREC", "GALAXYSURF", "GALLANTT", "GANDHAR", "GANECOS", "GANESHHOU", "GARFIBRES", "GARUDA", "GATEWAY", "GEMAROMA", "GENESYS", "GENUSPOWER", "GEOJITFSL", "GESHIP", "GHCL", "GHCLTEXTIL", "GICHSGFIN", "GICL", "GICRE", "GILLETTE", "GIPCL", "GLAND", "GLAXO", "GLENMARK", "GLOBUSSPR", "GMBREW", "GMDCLTD", "GMMPFAUDLR", "GMRAIRPORT", "GMRP&UI", "GNA", "GNFC", "GOCLCORP", "GOCOLORS", "GODAVARIB", "GODFRYPHLP", "GODIGIT", "GODREJAGRO", "GODREJCP", "GODREJIND", "GODREJPROP", "GOKEX", "GOKULAGRO", "GOLDADD", "GOLDIAM", "GOODLUCK", "GOPAL", "GPIL", "GPPL", "GPTHEALTH", "GPTINFRA", "GRANULES", "GRAPHITE", "GRASIM", "GRAVITA", "GREAVESCOT", "GREENLAM", "GREENPANEL", "GREENPLY", "GREENPOWER", "GRINDWELL", "GRINFRA", "GRMOVER", "GRSE", "GRWRHITECH", "GSFC", "GTLINFRA", "GUFICBIO", "GUJALKALI", "GUJENERGY", "GUJTHEM", "GULFOILLUB", "GULPOLY", "GVPIL", "GVT&D", "HAL", "HAPPSTMNDS", "HAPPYFORGE", "HARDWYN", "HARIOMPIPE", "HARSHA", "HATHWAY", "HATSUN", "HAVELLS", "HBLENGINE", "HCC", "HCG", "HCLTECH", "HDBFS", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEG", "HEIDELBERG", "HEMIPROP", "HERITGFOOD", "HEROMOTOCO", "HESTERBIO", "HEXT", "HFCL", "HGINFRA", "HIKAL", "HIMATSEIDE", "HINDALCO", "HINDCOPPER", "HINDOILEXP", "HINDPETRO", "HINDUNILVR", "HINDWAREAP", "HINDZINC", "HIRECT", "HITECH", "HLEGLAS", "HNDFDS", "HOMEFIRST", "HONASA", "HONAUT", "HPL", "HSCL", "HUBTOWN", "HUDCO", "HUHTAMAKI", "HYUNDAI", "IBULLSLTD", "ICEMAKE", "ICICIBANK", "ICICIGI", "ICICIPRULI", "ICIL", "ICRA", "IDBI", "IDEA", "IDEAFORGE", "IDFCFIRSTB", "IEX", "IFBIND", "IFCI", "IGARASHI", "IGIL", "IGL", "IGPL", "IIFL", "IIFLCAPS", "IITL", "IKIO", "IKS", "IMAGICAA", "IMFA", "INDGN", "INDHOTEL", "INDIACEM", "INDIAMART", "INDIANB", "INDIANHUME", "INDIASHLTR", "INDIGO", "INDIGOPNTS", "INDNIPPON", "INDOBORAX", "INDOCO", "INDOFARM", "INDOSTAR", "INDOTECH", "INDOTHAI", "INDRAMEDCO", "INDSWFTLAB", "INDUSINDBK", "INDUSTOWER", "INFOBEAN", "INFY", "INGERRAND", "INNOVACAP", "INOXGREEN", "INOXINDIA", "INOXWIND", "INSECTICID", "INTELLECT", "INTERARCH", "IOB", "IOC", "IOLCP", "IONEXCHANG", "IPCALAB", "IPL", "IRB", "IRCON", "IRCTC", "IREDA", "IRFC", "IRISDOREME", "IRMENERGY", "ISGEC", "ITC", "ITCHOTELS", "ITDC", "ITI", "IXIGO", "IZMO", "J&KBANK", "JAGRAN", "JAGSNPHARM", "JAIBALAJI", "JAICORPLTD", "JAMNAAUTO", "JASH", "JAYBARMARU", "JAYNECOIND", "JBMA", "JGCHEM", "JINDALPOLY", "JINDALSAW", "JINDALSTEL", "JINDRILL", "JINDWORLD", "JIOFIN", "JISLJALEQS", "JKCEMENT", "JKIL", "JKLAKSHMI", "JKPAPER", "JKTYRE", "JLHL", "JMFINANCIL", "JNKINDIA", "JPPOWER", "JSFB", "JSL", "JSLL", "JSWCEMENT", "JSWDULUX", "JSWENERGY", "JSWHL", "JSWINFRA", "JSWSTEEL", "JTEKTINDIA", "JTLIND", "JUBLCPL", "JUBLFOOD", "JUBLINGREA", "JUBLPHARMA", "JUNIPER", "JUSTDIAL", "JWL", "JYOTHYLAB", "JYOTICNC", "JYOTISTRUC", "KABRAEXTRU", "KAJARIACER", "KALAMANDIR", "KALPATARU", "KALYANKJIL", "KANSAINER", "KARURVYSYA", "KAYNES", "KCP", "KDDL", "KEC", "KECL", "KEI", "KELLTONTEC", "KERNEX", "KFINTECH", "KIMS", "KINGFA", "KIOCL", "KIRIINDUS", "KIRLOSBROS", "KIRLOSENG", "KIRLOSIND", "KIRLPNU", "KITEX", "KKCL", "KMEW", "KNRCON", "KOLTEPATIL", "KOPRAN", "KOTAKBANK", "KPEL", "KPIGREEN", "KPIL", "KPITTECH", "KPRMILL", "KRBL", "KRISHANA", "KRISHNADEF", "KRN", "KROSS", "KRSNAA", "KSB", "KSCL", "KSL", "KSOLVES", "KTKBANK", "LALPATHLAB", "LANDMARK", "LAOPALA", "LATENTVIEW", "LAURUSLABS", "LAXMIDENTL", "LAXMIINDIA", "LEMERITE", "LEMONTREE", "LGBBROSLTD", "LICHSGFIN", "LICI", "LINCOLN", "LINDEINDIA", "LLOYDSENGG", "LLOYDSENT", "LLOYDSME", "LMW", "LODHA", "LT", "LTF", "LTFOODS", "LTM", "LTTS", "LUMAXIND", "LUMAXTECH", "LUPIN", "LUXIND", "LXCHEM", "M&M", "M&MFIN", "MACPOWER", "MAHABANK", "MAHLIFE", "MAHLOG", "MAHSCOOTER", "MAHSEAMLES", "MAITHANALL", "MAMATA", "MANAKCOAT", "MANALIPETC", "MANAPPURAM", "MANCREDIT", "MANGLMCEM", "MANINDS", "MANINFRA", "MANKIND", "MANOMAY", "MANORAMA", "MANYAVAR", "MAPMYINDIA", "MARATHON", "MARICO", "MARINE", "MARKSANS", "MARUTI", "MASFIN", "MASTEK", "MASTERTR", "MAXESTATES", "MAXHEALTH", "MAYURUNIQ", "MAZDOCK", "MBAPL", "MBEL", "MCLEODRUSS", "MCLOUD", "MCX", "MEDANTA", "MEDIASSIST", "MEDPLUS", "METROBRAND", "METROPOLIS", "MFSL", "MGL", "MHRIL", "MIDHANI", "MINDACORP", "MINDTECK", "MMFL", "MMTC", "MOBIKWIK", "MODISONLTD", "MOIL", "MOL", "MOLDTKPAC", "MONARCH", "MONTECARLO", "MOREPENLAB", "MOSCHIP", "MOTHERSON", "MOTILALOFS", "MOTISONS", "MPHASIS", "MPSLTD", "MRF", "MRPL", "MSPL", "MSTCLTD", "MSUMI", "MTNL", "MUFIN", "MUKANDLTD", "MUNJALAU", "MUTHOOTFIN", "MUTHOOTMF", "MWL", "NACLIND", "NARMADA", "NATCOPHARM", "NATIONALUM", "NAUKRI", "NAVA", "NAVINFLUOR", "NAVKARCORP", "NAVNETEDUL", "NAZARA", "NBCC", "NCC", "NDRAUTO", "NDTV", "NELCAST", "NELCO", "NEOGEN", "NESCO", "NESTLEIND", "NETWEB", "NETWORK18", "NEULANDLAB", "NEWGEN", "NFL", "NGLFINE", "NH", "NHPC", "NIACL", "NIBE", "NIITLTD", "NIITMTS", "NILKAMAL", "NITCO", "NITINSPIN", "NIVABUPA", "NLCINDIA", "NMDC", "NOCIL", "NORTHARC", "NPST", "NRBBEARING", "NSIL", "NSLNISP", "NTPC", "NTPCGREEN", "NUCLEUS", "NUVAMA", "NUVOCO", "NYKAA", "OBEROIRLTY", "OFSS", "OIL", "OLAELEC", "OLECTRA", "OMAXE", "ONEPOINT", "ONESOURCE", "ONGC", "ONIDA", "ONWARDTEC", "OPTIEMUS", "ORCHPHARMA", "ORIENTCEM", "ORIENTELEC", "ORIENTHOT", "ORIENTTECH", "ORISSAMINE", "OSWALPUMPS", "PAGEIND", "PAISALO", "PANACEABIO", "PANAMAPET", "PARACABLES", "PARADEEP", "PARAGMILK", "PARAS", "PARKHOTELS", "PATANJALI", "PATELENG", "PATELRMART", "PAYTM", "PCBL", "PCJEWELLER", "PDSL", "PENIND", "PERSISTENT", "PETRONET", "PFC", "PFIZER", "PFOCUS", "PFS", "PGEL", "PGHH", "PGHL", "PGIL", "PHOENIXLTD", "PICCADIL", "PIDILITIND", "PIIND", "PITTIENG", "PIXTRANS", "PLATIND", "PNB", "PNBGILTS", "PNBHOUSING", "PNCINFRA", "PNGJL", "POCL", "POKARNA", "POLICYBZR", "POLYCAB", "POLYMED", "POLYPLEX", "POONAWALLA", "POWERGRID", "POWERINDIA", "POWERMECH", "PPLPHARMA", "PRAJIND", "PRAKASH", "PRECAM", "PRECWIRE", "PREMEXPLN", "PREMIERENE", "PRESTIGE", "PRICOLLTD", "PRINCEPIPE", "PRIVISCL", "PROSTARM", "PROTEAN", "PRSMJOHNSN", "PRUDENT", "PSB", "PSPPROJECT", "PTC", "PTCIL", "PURVA", "PVP", "PVRINOX", "QPOWER", "QUADFUTURE", "QUESS", "RACLGEAR", "RADHIKAJWE", "RADICO", "RAILTEL", "RAIN", "RAINBOW", "RAJRATAN", "RALLIS", "RAMCOCEM", "RAMCOIND", "RAMCOSYS", "RAMKY", "RAMRAT", "RANEHOLDIN", "RATEGAIN", "RATNAMANI", "RATNAVEER", "RAYMOND", "RAYMONDLSL", "RAYMONDREL", "RBA", "RBLBANK", "RBZJEWEL", "RCF", "RECLTD", "REDINGTON", "REDTAPE", "REFEX", "REGAAL", "RELAXO", "RELIANCE", "RELIGARE", "RELTD", "RENUKA", "REPCOHOME", "RESPONIND", "RGL", "RHIM", "RICOAUTO", "RIIL", "RISHABH", "RITCO", "RITES", "RKFORGE", "RML", "ROHLTD", "ROLEXRINGS", "ROSSARI", "ROSSTECH", "ROTO", "ROUTE", "RPEL", "RPGLIFE", "RPOWER", "RPSGVENT", "RPTECH", "RRKABEL", "RSYSTEMS", "RTNINDIA", "RTNPOWER", "RUPA", "RUSTOMJEE", "RVNL", "SAFARI", "SAGILITY", "SAIL", "SAILIFE", "SAKAR", "SAKSOFT", "SALASAR", "SALZERELEC", "SAMBHV", "SAMHI", "SAMMAANCAP", "SANATHAN", "SANDHAR", "SANDUMA", "SANGAMIND", "SANGHVIMOV", "SANOFI", "SANOFICONR", "SANSERA", "SANSTAR", "SAPPHIRE", "SARDAEN", "SAREGAMA", "SASKEN", "SATIN", "SBC", "SBCL", "SBFC", "SBICARD", "SBILIFE", "SBIN", "SCHAEFFLER", "SCHNEIDER", "SCI", "SCILAL", "SCODATUBES", "SDBL", "SEAMECLTD", "SENCO", "SENORES", "SEPC", "SERVOTECH", "SETL", "SFL", "SGFIN", "SHAH", "SHAILY", "SHAKTIPUMP", "SHALBY", "SHANTIGOLD", "SHARDACROP", "SHARDAMOTR", "SHAREINDIA", "SHILPAMED", "SHK", "SHOPERSTOP", "SHREECEM", "SHREEJISPG", "SHREEPUSHK", "SHRIPISTON", "SHRIRAMFIN", "SHRIRAMPPS", "SHYAMMETL", "SIEMENS", "SIGACHI", "SIGNATURE", "SIGNPOST", "SILGO", "SILVERTUC", "SIMPLEXINF", "SINDHUTRAD", "SIRCA", "SIS", "SIYSIL", "SJS", "SJVN", "SKFINDIA", "SKIPPER", "SKMEGGPROD", "SKYGOLD", "SMARTWORKS", "SMCGLOBAL", "SMLMAH", "SMSPHARMA", "SOBHA", "SOLARA", "SOLARINDS", "SOLEX", "SOMANYCERA", "SONACOMS", "SONATSOFTW", "SOTL", "SOUTHBANK", "SOUTHWEST", "SPAL", "SPANDANA", "SPARC", "SPIC", "SPLPETRO", "SPMLINFRA", "SPORTKING", "SRF", "SRM", "SSWL", "STANLEY", "STAR", "STARCEMENT", "STARHEALTH", "STEELCAS", "STEELXIND", "STOVEKRAFT", "STYLAMIND", "STYLEBAAZA", "STYRENIX", "SUBEXLTD", "SUBROS", "SUDARSCHEM", "SULA", "SUMICHEM", "SUNDARMFIN", "SUNDRMFAST", "SUNFLAG", "SUNPHARMA", "SUNTECK", "SUNTV", "SUPRAJIT", "SUPREMEIND", "SUPRIYA", "SURAJEST", "SURYAROSNI", "SURYODAY", "SUVEN", "SUYOG", "SUZLON", "SWANCORP", "SWARAJENG", "SWELECTES", "SWIGGY", "SWSOLAR", "SYMPHONY", "SYNCOMF", "SYNGENE", "SYRMA", "TAJGVK", "TALBROAUTO", "TANLA", "TARC", "TARIL", "TARSONS", "TASTYBITE", "TATACHEM", "TATACOMM", "TATACONSUM", "TATAELXSI", "TATAINVEST", "TATAPOWER", "TATASTEEL", "TATATECH", "TATVA", "TBOTEK", "TBZ", "TCI", "TCIEXP", "TCS", "TDPOWERSYS", "TEAMLEASE", "TECHM", "TECHNOE", "TEGA", "TEJASNET", "TEMBO", "TEXINFRA", "TEXRAIL", "TFCILTD", "THANGAMAYL", "THELEELA", "THERMAX", "THOMASCOOK", "THYROCARE", "TI", "TIIL", "TIINDIA", "TIMETECHNO", "TIMKEN", "TINNARUBR", "TIPSMUSIC", "TIRUMALCHM", "TITAGARH", "TITAN", "TMB", "TMPV", "TNPETRO", "TNPL", "TORNTPHARM", "TORNTPOWER", "TRANSRAILL", "TRAVELFOOD", "TRENT", "TRIDENT", "TRITURBINE", "TRIVENI", "TSFINV", "TTKPRESTIG", "TTML", "TVSELECT", "TVSHLTD", "TVSMOTOR", "TVSSCS", "TVSSRICHAK", "UBL", "UCOBANK", "UDS", "UFLEX", "UGROCAP", "UJJIVANSFB", "ULTRACEMCO", "UNICHEMLAB", "UNIECOM", "UNIMECH", "UNIONBANK", "UNIPARTS", "UNITDSPR", "UNITECH", "UNIVCABLES", "UNOMINDA", "UPL", "URJA", "USHAMART", "UTIAMC", "UTKARSHBNK", "UTTAMSUGAR", "V2RETAIL", "VADILALIND", "VAIBHAVGBL", "VARROC", "VBL", "VEDL", "VEEDOL", "VENKEYS", "VENTIVE", "VENUSPIPES", "VERANDA", "VESUVIUS", "VGUARD", "VIJAYA", "VIKRAMSOLR", "VIMTALABS", "VINATIORGA", "VINCOFE", "VINDHYATEL", "VIPIND", "VISHNU", "VIYASH", "VMART", "VMM", "VOLTAMP", "VOLTAS", "VPRPL", "VRLLOG", "VSSL", "VSTIND", "VSTTILLERS", "VTL", "WAAREEENER", "WAAREERTL", "WABAG", "WALCHANNAG", "WANBURY", "WEBELSOLAR", "WEL", "WELCORP", "WELENT", "WELSPUNLIV", "WENDT", "WESTLIFE", "WHEELS", "WHIRLPOOL", "WINDLAS", "WINDMACHIN", "WIPRO", "WOCKPHARMA", "WONDERLA", "WSTCSTPAPR", "XPROINDIA", "YASHO", "YATHARTH", "YATRA", "YESBANK", "ZAGGLE", "ZEEL", "ZENSARTECH", "ZENTEC", "ZFCVINDIA", "ZOTA", "ZUARI", "ZUARIIND", "ZYDUSLIFE", "ZYDUSWELL"]

print(f"""
  ------------------------------------------------------------------
   DOWNLOAD 3 YEARS OF INTRADAY DATA
  ------------------------------------------------------------------
   {len(SYMBOLS)} stocks · {INTERVAL} candles · {MONTHS_BACK} months
   Every NSE stock trading over Rs 1 crore a day.
   This takes about 90 minutes. You can close it and run it
   again later - it keeps whatever it has already finished.
   Saving to  DATA\\intraday_bars_3y.json
  ------------------------------------------------------------------
""")

# ---- connect, the same way the desk does -----------------------------------
keys = os.path.join(FEED, "kite_keys.txt")
tokf = os.path.join(FEED, "kite_token.json")
if not os.path.exists(keys) or not os.path.exists(tokf):
    print("  NOT CONNECTED TO ZERODHA.\n"
          "  Open the '5 LIVE FEED' folder and run connect.py first.")
    hold(1)

api_key = None
for line in open(keys, encoding="utf8"):
    if line.strip().startswith("api_key"):
        api_key = line.split("=", 1)[-1].strip()
tok = json.load(open(tokf, encoding="utf8"))
if tok.get("date") != date.today().isoformat():
    print(f"  TODAY'S LOGIN IS MISSING (the saved one is from {tok.get('date')}).\n"
          "  Run connect.py in '5 LIVE FEED', then start this again.")
    hold(1)

# INSTALL IT HERE IF IT IS MISSING, rather than sending you back to
# connect.py. Windows often has more than one Python, and the one that opens
# a .bat is not always the one connect.py installed into - so "run connect.py
# first" is not a fix, it is a loop. This installs into whichever Python is
# actually running this file.
try:
    from kiteconnect import KiteConnect
except ImportError:
    import subprocess
    print("  Installing Zerodha's connection library for this Python ...")
    print("  (Yellow WARNING lines below are harmless.)\n", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "--no-warn-script-location",
                    "kiteconnect"], check=False)
    try:
        import importlib, site
        try:
            site.main()
        except Exception:
            pass
        importlib.invalidate_caches()
        from kiteconnect import KiteConnect
    except ImportError:
        print("\n  " + "=" * 56)
        print("  Installed, but this window cannot see it yet.")
        print("  That is normal. Close this window and double-click the")
        print("  .bat again - it works the second time.")
        print("  " + "=" * 56)
        hold(1)
    print("  Installed.\n", flush=True)

kite = KiteConnect(api_key=api_key)
kite.set_access_token(tok["access_token"])
try:
    kite.quote(["NSE:RELIANCE"])
except Exception as e:
    print(f"  Zerodha refused the connection: {e}\n  Run connect.py again.")
    hold(1)
print("  Connected.\n")

# ---- what we already have --------------------------------------------------
have = {}
if os.path.exists(OUT):
    try:
        have = json.load(open(OUT, encoding="utf8")).get("bars", {})
        print(f"  Found {len(have)} stocks already downloaded - keeping them.\n")
    except Exception:
        have = {}

# ---- instrument tokens -----------------------------------------------------
print("  Reading the NSE instrument list ...", flush=True)
try:
    instr = kite.instruments("NSE")
except Exception as e:
    print(f"  Could not read the instrument list: {e}")
    hold(1)
TOK = {r["tradingsymbol"]: r["instrument_token"] for r in instr
       if r.get("instrument_type") == "EQ"}
print(f"  {len(TOK)} equity symbols.\n", flush=True)

# ---- download --------------------------------------------------------------
end = datetime.now()
start = end - timedelta(days=int(MONTHS_BACK * 30.5))
todo = [s for s in SYMBOLS if s not in have]
print(f"  {len(todo)} to download.\n", flush=True)

done = failed = 0
t0 = time.time()
for n, sym in enumerate(todo, 1):
    tk = TOK.get(sym)
    if not tk:
        failed += 1
        continue
    rows = []
    cur = start
    ok = True
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK), end)
        try:
            raw = kite.historical_data(tk, cur, nxt, INTERVAL) or []
        except Exception as e:
            if "many requests" in str(e).lower() or "too many" in str(e).lower():
                time.sleep(2.0)
                continue
            ok = False
            break
        for c in raw:
            rows.append([str(c["date"])[:16].replace("T", " "),
                         round(float(c["open"]), 2), round(float(c["high"]), 2),
                         round(float(c["low"]), 2), round(float(c["close"]), 2),
                         int(c.get("volume") or 0)])
        cur = nxt
        time.sleep(PACE)
    if ok and rows:
        have[sym] = rows
        done += 1
    else:
        failed += 1
    if n % 10 == 0 or n == len(todo):
        el = time.time() - t0
        left = (el / n) * (len(todo) - n)
        print(f"    {n}/{len(todo)}  {sym:<12} {done} ok, {failed} skipped"
              f"   about {left/60:.0f} min left", flush=True)
        # save as we go, so a crash never loses the work
        json.dump({"interval": INTERVAL, "saved": datetime.now().isoformat(),
                   "bars": have}, open(OUT, "w"))

json.dump({"interval": INTERVAL, "saved": datetime.now().isoformat(),
           "bars": have}, open(OUT, "w"))
size = os.path.getsize(OUT) / 1e6
tot = sum(len(v) for v in have.values())
print(f"""
  ------------------------------------------------------------------
   DONE
  ------------------------------------------------------------------
   {len(have)} stocks · {tot:,} candles · {size:.0f} MB
   Saved to  DATA\\intraday_bars_3y.json

   Tell Claude it is ready.
  ------------------------------------------------------------------
""")
hold(0)
