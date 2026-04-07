from app.services.enterprise_gre_service import (
    EnterpriseGreValidator,
    EnterpriseGreValidationResult,
)

validator = EnterpriseGreValidator()

res: EnterpriseGreValidationResult = validator.validate_phone("09122524611")

print(res.gre_status)
print(res.message)
print(res.normalized_phone)


