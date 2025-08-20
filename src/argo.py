
from roofhelper.defaultlogging import setup_logging

log = setup_logging()

if __name__ == "__main__":
    from argo import createbagdb
    from argo import fixcityjson
    from argo import geluid
    from argo import height
    from argo import lazdb
    from argo import lazsplit
    from argo import pdokupdategeluid
    from argo import roofer
    from argo import tyler
    from argo import validatecityjson

    log.info("Generating Argo workflows...")

    log.info("Generating createbagdb workflow...")
    createbagdb.generate_workflow()

    log.info("Generating fixcityjson workflow...")
    fixcityjson.generate_workflow()

    log.info("Generating geluid workflow...")
    geluid.generate_workflow()

    log.info("Generating height workflow...")
    height.generate_workflow()

    log.info("Generating lazdb workflow...")
    lazdb.generate_workflow()

    log.info("Generating lazsplit workflow...")
    lazsplit.generate_workflow()

    log.info("Generating pdokupdategeluid workflow...")
    pdokupdategeluid.generate_workflow()

    log.info("Generating roofer workflow...")
    roofer.generate_workflow()

    log.info("Generating tyler workflow...")
    tyler.generate_workflow()

    log.info("Generating validatecityjson workflow...")
    validatecityjson.generate_workflow()

    log.info("Finished generating Argo workflows.")
