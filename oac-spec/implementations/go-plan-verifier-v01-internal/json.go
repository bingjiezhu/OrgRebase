package main

import (
	"bytes"
	"errors"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

type jsonKind uint8

const (
	kindNull jsonKind = iota
	kindBool
	kindString
	kindNumber
	kindArray
	kindObject
)

type jsonValue struct {
	kind   jsonKind
	bool   bool
	string string
	number float64
	array  []jsonValue
	object []jsonMember
}

type jsonMember struct {
	key   string
	value jsonValue
}

type jsonDomainError struct{ message string }

func (e *jsonDomainError) Error() string { return e.message }

// Invalid UTF-8 is JSON grammar failure at the SealedResource boundary. It is
// intentionally distinct from a decoded invalid Unicode scalar such as a lone
// surrogate, which is inside JSON string grammar but outside I-JSON.
type jsonUTF8Error struct{ message string }

func (e *jsonUTF8Error) Error() string { return e.message }

type jsonDepthError struct{}

func (e *jsonDepthError) Error() string { return "JSON value exceeds maxJsonDepth" }

type duplicateKeyError struct{ key string }

func (e *duplicateKeyError) Error() string { return "duplicate JSON object key: " + e.key }

type jsonParser struct {
	data  []byte
	pos   int
	depth int
}

func parseJSON(data []byte) (jsonValue, error) {
	if !utf8.Valid(data) {
		return jsonValue{}, &jsonUTF8Error{message: "invalid UTF-8"}
	}
	p := jsonParser{data: data}
	p.skipWhitespace()
	v, err := p.parseValue()
	if err != nil {
		return jsonValue{}, err
	}
	p.skipWhitespace()
	if p.pos != len(data) {
		return jsonValue{}, errors.New("trailing JSON data")
	}
	return v, nil
}

func canonicalizeJSON(data []byte) ([]byte, error) {
	v, err := parseJSON(data)
	if err != nil {
		return nil, err
	}
	return canonicalBytes(v), nil
}

func (p *jsonParser) parseValue() (jsonValue, error) {
	p.depth++
	defer func() { p.depth-- }()
	if p.depth > maxJSONDepth {
		return jsonValue{}, &jsonDepthError{}
	}
	if p.pos >= len(p.data) {
		return jsonValue{}, errors.New("unexpected end of JSON")
	}
	switch p.data[p.pos] {
	case 'n':
		if !p.literal("null") {
			return jsonValue{}, errors.New("invalid JSON literal")
		}
		return jsonValue{kind: kindNull}, nil
	case 't':
		if !p.literal("true") {
			return jsonValue{}, errors.New("invalid JSON literal")
		}
		return jsonValue{kind: kindBool, bool: true}, nil
	case 'f':
		if !p.literal("false") {
			return jsonValue{}, errors.New("invalid JSON literal")
		}
		return jsonValue{kind: kindBool}, nil
	case '"':
		s, err := p.parseString()
		return jsonValue{kind: kindString, string: s}, err
	case '[':
		return p.parseArray()
	case '{':
		return p.parseObject()
	default:
		if p.data[p.pos] == '-' || isDigit(p.data[p.pos]) {
			return p.parseNumber()
		}
		return jsonValue{}, errors.New("invalid JSON value")
	}
}

func (p *jsonParser) parseArray() (jsonValue, error) {
	p.pos++
	p.skipWhitespace()
	values := make([]jsonValue, 0)
	if p.take(']') {
		return jsonValue{kind: kindArray, array: values}, nil
	}
	for {
		v, err := p.parseValue()
		if err != nil {
			return jsonValue{}, err
		}
		values = append(values, v)
		p.skipWhitespace()
		if p.take(']') {
			return jsonValue{kind: kindArray, array: values}, nil
		}
		if !p.take(',') {
			return jsonValue{}, errors.New("invalid JSON array")
		}
		p.skipWhitespace()
	}
}

func (p *jsonParser) parseObject() (jsonValue, error) {
	p.pos++
	p.skipWhitespace()
	members := make([]jsonMember, 0)
	seen := map[string]struct{}{}
	if p.take('}') {
		return jsonValue{kind: kindObject, object: members}, nil
	}
	for {
		if p.pos >= len(p.data) || p.data[p.pos] != '"' {
			return jsonValue{}, errors.New("invalid JSON object key")
		}
		key, err := p.parseString()
		if err != nil {
			return jsonValue{}, err
		}
		if _, ok := seen[key]; ok {
			return jsonValue{}, &duplicateKeyError{key: key}
		}
		seen[key] = struct{}{}
		p.skipWhitespace()
		if !p.take(':') {
			return jsonValue{}, errors.New("missing JSON object colon")
		}
		p.skipWhitespace()
		v, err := p.parseValue()
		if err != nil {
			return jsonValue{}, err
		}
		members = append(members, jsonMember{key: key, value: v})
		p.skipWhitespace()
		if p.take('}') {
			return jsonValue{kind: kindObject, object: members}, nil
		}
		if !p.take(',') {
			return jsonValue{}, errors.New("invalid JSON object")
		}
		p.skipWhitespace()
	}
}

func (p *jsonParser) parseString() (string, error) {
	p.pos++
	var out strings.Builder
	for p.pos < len(p.data) {
		c := p.data[p.pos]
		if c == '"' {
			p.pos++
			return out.String(), nil
		}
		if c < 0x20 {
			return "", errors.New("unescaped control in JSON string")
		}
		if c == '\\' {
			p.pos++
			if p.pos >= len(p.data) {
				return "", errors.New("truncated JSON escape")
			}
			escape := p.data[p.pos]
			p.pos++
			switch escape {
			case '"', '\\', '/':
				out.WriteByte(escape)
			case 'b':
				out.WriteByte('\b')
			case 'f':
				out.WriteByte('\f')
			case 'n':
				out.WriteByte('\n')
			case 'r':
				out.WriteByte('\r')
			case 't':
				out.WriteByte('\t')
			case 'u':
				hi, err := p.hex4()
				if err != nil {
					return "", err
				}
				if hi >= 0xd800 && hi <= 0xdbff {
					if p.pos+2 > len(p.data) || p.data[p.pos] != '\\' || p.data[p.pos+1] != 'u' {
						return "", &jsonDomainError{message: "unpaired high surrogate"}
					}
					p.pos += 2
					lo, err := p.hex4()
					if err != nil || lo < 0xdc00 || lo > 0xdfff {
						return "", &jsonDomainError{message: "unpaired high surrogate"}
					}
					out.WriteRune(utf16.DecodeRune(rune(hi), rune(lo)))
				} else if hi >= 0xdc00 && hi <= 0xdfff {
					return "", &jsonDomainError{message: "unpaired low surrogate"}
				} else {
					out.WriteRune(rune(hi))
				}
			default:
				return "", errors.New("invalid JSON escape")
			}
			continue
		}
		if c < utf8.RuneSelf {
			out.WriteByte(c)
			p.pos++
			continue
		}
		r, size := utf8.DecodeRune(p.data[p.pos:])
		out.WriteRune(r)
		p.pos += size
	}
	return "", errors.New("unterminated JSON string")
}

func (p *jsonParser) hex4() (uint16, error) {
	if p.pos+4 > len(p.data) {
		return 0, errors.New("truncated Unicode escape")
	}
	var n uint16
	for i := 0; i < 4; i++ {
		v, ok := hexNibble(p.data[p.pos+i])
		if !ok {
			return 0, errors.New("invalid Unicode escape")
		}
		n = n<<4 | uint16(v)
	}
	p.pos += 4
	return n, nil
}

func (p *jsonParser) parseNumber() (jsonValue, error) {
	start := p.pos
	if p.take('-') && p.pos >= len(p.data) {
		return jsonValue{}, errors.New("invalid JSON number")
	}
	if p.take('0') {
		if p.pos < len(p.data) && isDigit(p.data[p.pos]) {
			return jsonValue{}, errors.New("leading zero in JSON number")
		}
	} else {
		if p.pos >= len(p.data) || p.data[p.pos] < '1' || p.data[p.pos] > '9' {
			return jsonValue{}, errors.New("invalid JSON number")
		}
		for p.pos < len(p.data) && isDigit(p.data[p.pos]) {
			p.pos++
		}
	}
	if p.take('.') {
		if p.pos >= len(p.data) || !isDigit(p.data[p.pos]) {
			return jsonValue{}, errors.New("invalid JSON fraction")
		}
		for p.pos < len(p.data) && isDigit(p.data[p.pos]) {
			p.pos++
		}
	}
	if p.pos < len(p.data) && (p.data[p.pos] == 'e' || p.data[p.pos] == 'E') {
		p.pos++
		if p.pos < len(p.data) && (p.data[p.pos] == '+' || p.data[p.pos] == '-') {
			p.pos++
		}
		if p.pos >= len(p.data) || !isDigit(p.data[p.pos]) {
			return jsonValue{}, errors.New("invalid JSON exponent")
		}
		for p.pos < len(p.data) && isDigit(p.data[p.pos]) {
			p.pos++
		}
	}
	n, err := strconv.ParseFloat(string(p.data[start:p.pos]), 64)
	if err != nil || math.IsInf(n, 0) || math.IsNaN(n) {
		return jsonValue{}, &jsonDomainError{message: "number outside IEEE-754 domain"}
	}
	return jsonValue{kind: kindNumber, number: n}, nil
}

func (p *jsonParser) literal(s string) bool {
	if len(p.data)-p.pos < len(s) || string(p.data[p.pos:p.pos+len(s)]) != s {
		return false
	}
	p.pos += len(s)
	return true
}

func (p *jsonParser) skipWhitespace() {
	for p.pos < len(p.data) && strings.ContainsRune(" \t\n\r", rune(p.data[p.pos])) {
		p.pos++
	}
}

func (p *jsonParser) take(c byte) bool {
	if p.pos < len(p.data) && p.data[p.pos] == c {
		p.pos++
		return true
	}
	return false
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func canonicalBytes(v jsonValue) []byte {
	var out bytes.Buffer
	appendCanonical(&out, v)
	return out.Bytes()
}

func appendCanonical(out *bytes.Buffer, v jsonValue) {
	switch v.kind {
	case kindNull:
		out.WriteString("null")
	case kindBool:
		if v.bool {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case kindString:
		appendCanonicalString(out, v.string)
	case kindNumber:
		out.WriteString(formatECMAScriptNumber(v.number))
	case kindArray:
		out.WriteByte('[')
		for i, item := range v.array {
			if i > 0 {
				out.WriteByte(',')
			}
			appendCanonical(out, item)
		}
		out.WriteByte(']')
	case kindObject:
		members := append([]jsonMember(nil), v.object...)
		sort.Slice(members, func(i, j int) bool { return utf16Less(members[i].key, members[j].key) })
		out.WriteByte('{')
		for i, member := range members {
			if i > 0 {
				out.WriteByte(',')
			}
			appendCanonicalString(out, member.key)
			out.WriteByte(':')
			appendCanonical(out, member.value)
		}
		out.WriteByte('}')
	}
}

func appendCanonicalString(out *bytes.Buffer, s string) {
	out.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			out.WriteString("\\\"")
		case '\\':
			out.WriteString("\\\\")
		case '\b':
			out.WriteString("\\b")
		case '\t':
			out.WriteString("\\t")
		case '\n':
			out.WriteString("\\n")
		case '\f':
			out.WriteString("\\f")
		case '\r':
			out.WriteString("\\r")
		default:
			if r < 0x20 {
				const hex = "0123456789abcdef"
				out.WriteString("\\u00")
				out.WriteByte(hex[byte(r)>>4])
				out.WriteByte(hex[byte(r)&15])
			} else {
				out.WriteRune(r)
			}
		}
	}
	out.WriteByte('"')
}

func formatECMAScriptNumber(value float64) string {
	if value == 0 {
		return "0"
	}
	prefix := ""
	if value < 0 {
		prefix, value = "-", -value
	}
	exp := strconv.FormatFloat(value, 'e', -1, 64)
	sep := strings.IndexByte(exp, 'e')
	mantissa := exp[:sep]
	e, _ := strconv.Atoi(exp[sep+1:])
	digits := strings.ReplaceAll(mantissa, ".", "")
	k, n := len(digits), e+1
	switch {
	case k <= n && n <= 21:
		return prefix + digits + strings.Repeat("0", n-k)
	case 0 < n && n <= 21:
		return prefix + digits[:n] + "." + digits[n:]
	case -6 < n && n <= 0:
		return prefix + "0." + strings.Repeat("0", -n) + digits
	default:
		coefficient := digits[:1]
		if len(digits) > 1 {
			coefficient += "." + digits[1:]
		}
		scientific := n - 1
		sign := ""
		if scientific >= 0 {
			sign = "+"
		}
		return prefix + coefficient + "e" + sign + strconv.Itoa(scientific)
	}
}

func utf16Less(a, b string) bool {
	left, right := utf16.Encode([]rune(a)), utf16.Encode([]rune(b))
	limit := len(left)
	if len(right) < limit {
		limit = len(right)
	}
	for i := 0; i < limit; i++ {
		if left[i] != right[i] {
			return left[i] < right[i]
		}
	}
	return len(left) < len(right)
}

func hexNibble(c byte) (byte, bool) {
	switch {
	case c >= '0' && c <= '9':
		return c - '0', true
	case c >= 'a' && c <= 'f':
		return c - 'a' + 10, true
	case c >= 'A' && c <= 'F':
		return c - 'A' + 10, true
	default:
		return 0, false
	}
}

func jsonObject(members ...jsonMember) jsonValue { return jsonValue{kind: kindObject, object: members} }
func jsonString(s string) jsonValue              { return jsonValue{kind: kindString, string: s} }
func jsonBool(b bool) jsonValue                  { return jsonValue{kind: kindBool, bool: b} }
func jsonNull() jsonValue                        { return jsonValue{kind: kindNull} }
func jsonArrayStrings(values []string) jsonValue {
	items := make([]jsonValue, len(values))
	for i, value := range values {
		items[i] = jsonString(value)
	}
	return jsonValue{kind: kindArray, array: items}
}
